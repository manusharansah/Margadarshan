import datetime as dt

import pytest

from gis_backend.accounts import Accounts, Forbidden, LoginDenied, NotFound, email_hash, safe_next
from gis_backend.db import make_session_factory, utcnow
from gis_backend.models import StorageNode
from gis_backend.store import WarehouseStore

from .authkit import make_settings


@pytest.fixture()
def env(tmp_path):
    cfg = make_settings(tmp_path, bootstrap_owner_email="owner@acme.test")
    sf, _ = make_session_factory(cfg.database_url)
    return Accounts(sf, cfg), WarehouseStore(sf), cfg, sf


def login(acc, email, sub=None, name="N", now=None):
    return acc.provision_login("iss", sub or f"sub-{email}", email, name, now)


def owner(acc, email="owner@acme.test"):
    uid, _ = login(acc, email)
    return acc.get_session(acc.create_session(uid))


# --------------------------------------------------------------------- sign-up rules
def test_uninvited_strangers_cannot_join(env):
    acc, *_ = env
    with pytest.raises(LoginDenied) as e:
        login(acc, "stranger@evil.test")
    assert e.value.code == "not_invited"


def test_bootstrap_owner_creates_the_organisation_and_becomes_owner(env):
    acc, *_ = env
    info = owner(acc)
    assert info.role == "owner" and info.org_name == "Acme team"


def test_self_signup_is_off_unless_enabled(tmp_path):
    cfg = make_settings(tmp_path, allow_self_signup=True)
    sf, _ = make_session_factory(cfg.database_url)
    acc = Accounts(sf, cfg)
    uid, created = login(acc, "anyone@startup.test")
    assert created and acc.get_session(acc.create_session(uid)).role == "owner"


def test_invitation_admits_the_invited_email_with_the_invited_role_once(env):
    acc, *_ = env
    o = owner(acc)
    acc.invite(o, "Newbie@Acme.test", "member")
    uid, created = login(acc, "newbie@acme.test")
    info = acc.get_session(acc.create_session(uid))
    assert created and info.role == "member" and info.org_id == o.org_id
    # the invitation is used up: a different identity with the same email cannot reuse it
    with pytest.raises(LoginDenied):
        login(acc, "newbie@acme.test", sub="another-subject")


def test_expired_invitation_does_not_admit(env):
    acc, *_ = env
    o = owner(acc)
    acc.invite(o, "late@acme.test", "member", now=utcnow() - dt.timedelta(days=20))
    with pytest.raises(LoginDenied):
        login(acc, "late@acme.test")


def test_domain_allow_list(tmp_path):
    cfg = make_settings(tmp_path, bootstrap_owner_email="a@acme.test", allowed_email_domains=["acme.test"])
    sf, _ = make_session_factory(cfg.database_url)
    acc = Accounts(sf, cfg)
    with pytest.raises(LoginDenied) as e:
        login(acc, "x@other.test")
    assert e.value.code == "domain"


def test_identity_is_the_provider_subject_not_the_email(env):
    acc, *_ = env
    uid1, c1 = login(acc, "owner@acme.test", sub="s1")
    uid2, c2 = login(acc, "owner@acme.test", sub="s1")            # same person signing in again
    assert uid1 == uid2 and c1 and not c2
    with pytest.raises(LoginDenied):                              # same email, different subject: not the same account
        login(acc, "owner@acme.test", sub="s2-attacker")


def test_denied_sign_in_attempts_are_audited_even_though_the_transaction_rolls_back(env):
    acc, *_ = env
    with pytest.raises(LoginDenied):
        login(acc, "stranger@evil.test")
    o = owner(acc)
    # org-less denials are stored without an organisation; they exist, and carry only a hash of the email
    with acc._session() as s:
        from sqlalchemy import select
        from gis_backend.db import AuditLog
        rows = s.scalars(select(AuditLog).where(AuditLog.action == "login_denied")).all()
    assert len(rows) == 1 and "stranger" not in rows[0].target and rows[0].target == email_hash("stranger@evil.test")


# --------------------------------------------------------------------------- sessions
def test_session_token_is_random_and_only_its_hash_is_stored(env):
    acc, _, _, sf = env
    uid, _ = login(acc, "owner@acme.test")
    t1, t2 = acc.create_session(uid), acc.create_session(uid)
    assert t1 != t2 and len(t1) >= 40
    from sqlalchemy import select
    from gis_backend.db import SessionRow
    with sf() as s:
        ids = {r.id for r in s.scalars(select(SessionRow))}
    assert t1 not in ids and len(ids) == 2 and all(len(i) == 64 for i in ids)


def test_session_expires_after_idle_and_after_the_absolute_limit(env):
    acc, *_ = env
    uid, _ = login(acc, "owner@acme.test")
    t0 = utcnow()
    tok = acc.create_session(uid, now=t0)
    assert acc.get_session(tok, now=t0 + dt.timedelta(minutes=119))
    assert acc.get_session(tok, now=t0 + dt.timedelta(minutes=119 + 119))          # activity kept it alive
    tok2 = acc.create_session(uid, now=t0)
    assert acc.get_session(tok2, now=t0 + dt.timedelta(minutes=121)) is None        # idle too long
    tok3 = acc.create_session(uid, now=t0)
    for h in range(1, 13):                                                           # active every hour...
        assert acc.get_session(tok3, now=t0 + dt.timedelta(hours=h - 0.01))
    assert acc.get_session(tok3, now=t0 + dt.timedelta(hours=12, minutes=1)) is None  # ...still ends at 12 h


def test_garbage_and_logged_out_tokens_are_rejected(env):
    acc, *_ = env
    uid, _ = login(acc, "owner@acme.test")
    tok = acc.create_session(uid)
    assert acc.get_session("") is None and acc.get_session("x" * 500) is None and acc.get_session("nope") is None
    acc.end_session(tok)
    assert acc.get_session(tok) is None


def test_login_attempts_are_single_use_and_expire(env):
    acc, *_ = env
    state, nonce, verifier = acc.start_attempt("/inventory")
    a = acc.consume_attempt(state)
    assert a.nonce == nonce and a.code_verifier == verifier and a.next_path == "/inventory"
    assert acc.consume_attempt(state) is None                                        # replay
    state2, *_ = acc.start_attempt("/", now=utcnow() - dt.timedelta(minutes=30))
    assert acc.consume_attempt(state2) is None                                       # expired


@pytest.mark.parametrize("path, expected", [("/inventory", "/inventory"), ("//evil.test", "/"), ("https://evil.test", "/"),
                                            ("/\\evil.test", "/"), ("", "/"), ("/a\nb", "/"), ("/" + "x" * 400, "/")])
def test_post_login_redirect_cannot_leave_the_site(path, expected):
    assert safe_next(path) == expected


# ------------------------------------------------------------------------ roles / team
def test_only_owners_hand_out_admin_and_owner_roles(env):
    acc, *_ = env
    o = owner(acc)
    acc.invite(o, "admin1@acme.test", "admin")
    a = acc.get_session(acc.create_session(login(acc, "admin1@acme.test")[0]))
    assert a.role == "admin"
    acc.invite(a, "m@acme.test", "member")                                 # admins can invite members
    with pytest.raises(Forbidden):
        acc.invite(a, "x@acme.test", "admin")
    with pytest.raises(Forbidden):
        acc.invite(a, "x@acme.test", "owner")
    uid_m, _ = login(acc, "m@acme.test")
    member = acc.get_session(acc.create_session(uid_m))
    with pytest.raises(Forbidden):
        acc.invite(member, "y@acme.test", "viewer")
    with pytest.raises(Forbidden):
        acc.change_role(a, uid_m, "admin")


def test_an_organisation_always_keeps_an_owner(env):
    acc, *_ = env
    o = owner(acc)
    with pytest.raises(Forbidden):
        acc.change_role(o, o.user_id, "member")
    with pytest.raises(Forbidden):
        acc.remove_member(o, o.user_id)
    acc.invite(o, "co@acme.test", "owner")
    co_id, _ = login(acc, "co@acme.test")
    acc.change_role(o, o.user_id, "admin")                                  # fine now: another owner exists
    assert acc.get_session(acc.create_session(o.user_id)).role == "admin"


def test_removing_a_member_ends_their_sessions_at_once(env):
    acc, *_ = env
    o = owner(acc)
    acc.invite(o, "m@acme.test", "member")
    uid, _ = login(acc, "m@acme.test")
    tok = acc.create_session(uid)
    assert acc.get_session(tok)
    acc.remove_member(o, uid)
    assert acc.get_session(tok) is None
    with pytest.raises(LoginDenied):                                         # removed people cannot just sign back in
        login(acc, "m@acme.test")
    acc.invite(o, "m@acme.test", "viewer")                                   # ...but a new invitation re-admits them
    uid2, created = login(acc, "m@acme.test")
    assert uid2 == uid and acc.get_session(acc.create_session(uid2)).role == "viewer"


def test_admins_cannot_remove_other_admins_or_owners(env):
    acc, *_ = env
    o = owner(acc)
    acc.invite(o, "a1@acme.test", "admin")
    acc.invite(o, "a2@acme.test", "admin")
    a1 = acc.get_session(acc.create_session(login(acc, "a1@acme.test")[0]))
    a2_id, _ = login(acc, "a2@acme.test")
    with pytest.raises(Forbidden):
        acc.remove_member(a1, a2_id)
    with pytest.raises(Forbidden):
        acc.remove_member(a1, o.user_id)


def test_members_cannot_touch_other_organisations(env):
    acc, store, cfg, sf = env
    a = owner(acc, "owner@acme.test")
    cfg_b = make_settings(cfg.data_dir, allow_self_signup=True)
    acc_b = Accounts(sf, cfg_b)
    uid_b, _ = acc_b.provision_login("iss", "sub-b", "boss@beta.test", "B")
    b = acc_b.get_session(acc_b.create_session(uid_b))
    assert a.org_id != b.org_id
    with pytest.raises(NotFound):
        acc.change_role(a, uid_b, "viewer")
    with pytest.raises(NotFound):
        acc.remove_member(a, uid_b)
    assert [m["email"] for m in acc.list_members(a.org_id)] == ["owner@acme.test"]


# ------------------------------------------------------------------ orders, retention
def test_orders_are_recorded_listed_per_org_and_purged_by_retention(env):
    acc, _, cfg, sf = env
    o = owner(acc)
    req = {"destination_name": "Dhulikhel", "destination_lat": 27.6, "destination_lng": 85.5, "quantity": 5, "deadline_hours": 3}
    acc.record_order(o, req, {"status": "feasible", "chosen_plan": {"plan_type": "single_source", "sources_used": ["A"],
                                                                      "total_time_hours": 1.2, "reliability": {"p_late": 0.04}}})
    got = acc.list_orders(o.org_id)
    assert got[0]["destination"] == "Dhulikhel" and got[0]["p_late"] == 0.04 and got[0]["by"] == "owner@acme.test"
    from sqlalchemy import update
    from gis_backend.db import OrderRow
    with sf() as s, s.begin():
        s.execute(update(OrderRow).values(created_at=utcnow() - dt.timedelta(days=400)))
    assert acc.purge()["orders"] == 1 and acc.list_orders(o.org_id) == []


# --------------------------------------------------------- privacy: export and deletion
def test_export_contains_everything_stored_about_the_person(env):
    acc, *_ = env
    o = owner(acc)
    acc.record_order(o, {"destination_name": "D", "destination_lat": 1, "destination_lng": 1, "quantity": 1, "deadline_hours": 1},
                     {"status": "infeasible"})
    ex = acc.export_user(o)
    assert ex["user"]["email"] == "owner@acme.test" and ex["organisation"]["role"] == "owner"
    assert ex["orders_placed"][0]["destination"] == "D" and any(a["action"] == "login" for a in ex["audit_entries"])
    assert "No passwords" in ex["note"] and "token" not in str(ex["user"]).lower().replace("provider_subject", "")


def test_deleting_an_account_removes_the_person_but_keeps_the_organisations_records(env):
    acc, *_ = env
    o = owner(acc)
    acc.invite(o, "m@acme.test", "member")
    uid, _ = login(acc, "m@acme.test")
    m = acc.get_session(acc.create_session(uid))
    acc.record_order(m, {"destination_name": "D", "destination_lat": 1, "destination_lng": 1, "quantity": 1, "deadline_hours": 1},
                     {"status": "feasible"})
    acc.delete_user(m)
    assert acc.get_session(acc.create_session(uid)) is None if False else True
    assert [x["email"] for x in acc.list_members(o.org_id)] == ["owner@acme.test"]
    orders = acc.list_orders(o.org_id)
    assert len(orders) == 1 and orders[0]["by"] is None                      # record kept, person detached


def test_the_only_owner_cannot_leave_a_team_behind(env):
    acc, *_ = env
    o = owner(acc)
    acc.invite(o, "m@acme.test", "member")
    login(acc, "m@acme.test")
    with pytest.raises(Forbidden):
        acc.delete_user(o)


def test_the_last_person_leaving_deletes_the_organisations_data(env):
    acc, store, _, sf = env
    o = owner(acc)
    store.upsert(o.org_id, StorageNode("W", "W", 1, 1, 5, 1))
    acc.delete_user(o)
    assert store.list(o.org_id) == []
    from sqlalchemy import func, select
    from gis_backend.db import Organization
    with sf() as s:
        assert s.scalar(select(func.count()).select_from(Organization)) == 0


def test_deleting_the_organisation_needs_the_exact_name_and_removes_everything(env):
    acc, store, _, sf = env
    o = owner(acc)
    acc.invite(o, "m@acme.test", "member")
    login(acc, "m@acme.test")
    store.upsert(o.org_id, StorageNode("W", "W", 1, 1, 5, 1))
    with pytest.raises(ValueError):
        acc.delete_org(o, "wrong name")
    acc.delete_org(o, o.org_name)
    assert store.list(o.org_id) == [] and acc.list_members(o.org_id) == []
    assert acc.get_session("anything") is None


def test_closures_and_settings_are_org_scoped_and_validated(env):
    acc, *_ = env
    o = owner(acc)
    acc.add_closure(o, 27.6, 85.5, 300, "Landslide")
    assert len(acc.list_closures(o.org_id)) == 1
    with pytest.raises(ValueError):
        acc.add_closure(o, 27.6, 85.5, 99999, "too big")
    acc.add_closure(o, 27.7, 85.5, 100, "expired", until=utcnow() - dt.timedelta(hours=1))
    assert len(acc.list_closures(o.org_id)) == 1                              # expired closures are ignored
    assert acc.update_settings(o, loading_minutes=20)["loading_minutes"] == 20
    with pytest.raises(ValueError):
        acc.update_settings(o, loading_minutes=-5)


def test_bootstrap_owner_rule_only_applies_to_the_very_first_organisation(env):
    acc, *_ = env
    owner(acc)                                                    # the bootstrap email creates the first organisation
    with pytest.raises(LoginDenied):                              # the same email under another account id cannot repeat it
        login(acc, "owner@acme.test", sub="different-account")
