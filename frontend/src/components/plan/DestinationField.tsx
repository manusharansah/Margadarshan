import { useEffect, useRef, useState } from "react";
import { usePlaces } from "@/lib/api";
import type { Pin } from "@/components/map/RouteMap";

interface Props {
  value: Pin | null;
  onChange: (pin: Pin | null) => void;
}

/** Destination input in the original floating-label style: search a place, or click the map. */
export function DestinationField({ value, onChange }: Props) {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLLabelElement>(null);
  const { data: places = [], isFetching } = usePlaces(debounced);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(text), 200);
    return () => clearTimeout(t);
  }, [text]);

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <label
      ref={box}
      className="relative block rounded-md border border-panel-border px-3 pt-2 pb-2.5"
    >
      <span className="mb-0.5 block text-[11px] text-panel-muted">
        Destination (town, or click the map)
      </span>
      {value ? (
        <span className="flex items-center justify-between gap-2">
          <span className="min-w-0">
            <span className="block truncate text-[15px]">{value.name}</span>
            <span className="block text-[11px] text-panel-muted tabular-nums">
              {value.lat.toFixed(4)}, {value.lng.toFixed(4)}
            </span>
          </span>
          <button
            type="button"
            onClick={() => {
              onChange(null);
              setText("");
            }}
            className="shrink-0 text-[12px] text-panel-muted hover:text-panel-foreground"
            aria-label="Clear destination"
          >
            Clear
          </button>
        </span>
      ) : (
        <>
          <input
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            placeholder="Search e.g. Dhulikhel"
            aria-label="Destination"
            autoComplete="off"
            className="w-full bg-transparent text-[15px] text-panel-foreground outline-none placeholder:text-panel-muted"
          />
          {open && debounced.trim().length >= 2 && (
            <ul className="absolute left-0 right-0 top-full z-[1000] mt-1 max-h-60 overflow-auto rounded-md border border-border bg-popover py-1 text-popover-foreground shadow-lg">
              {places.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    className="flex w-full items-baseline justify-between px-3 py-2 text-left text-[14px] hover:bg-accent"
                    onClick={() => {
                      onChange({ name: p.name, lat: p.lat, lng: p.lng });
                      setOpen(false);
                    }}
                  >
                    <span>{p.name}</span>
                    <span className="text-[11px] capitalize text-muted-foreground">{p.kind}</span>
                  </button>
                </li>
              ))}
              {!places.length && (
                <li className="px-3 py-2 text-[13px] text-muted-foreground">
                  {isFetching ? "Searching…" : "No match — click the map instead"}
                </li>
              )}
            </ul>
          )}
        </>
      )}
    </label>
  );
}
