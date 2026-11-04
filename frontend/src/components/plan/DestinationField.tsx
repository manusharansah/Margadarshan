import { useEffect, useRef, useState } from "react";
import { MapPin, X } from "lucide-react";
import { usePlaces } from "@/lib/api";
import type { Pin } from "@/components/map/RouteMap";

interface Props {
  value: Pin | null;
  onChange: (pin: Pin | null) => void;
}

/** Search OpenStreetMap place names from the loaded network, or click the map. */
export function DestinationField({ value, onChange }: Props) {
  const [text, setText] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
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

  if (value) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-input bg-card px-3 py-2.5">
        <MapPin className="size-4 shrink-0 text-hazard" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{value.name}</p>
          <p className="text-[11px] text-muted-foreground tabular-nums">
            {value.lat.toFixed(4)}, {value.lng.toFixed(4)}
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            onChange(null);
            setText("");
          }}
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label="Clear destination"
        >
          <X className="size-4" />
        </button>
      </div>
    );
  }

  return (
    <div ref={box} className="relative">
      <input
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="Search a town or village, or click the map"
        className="w-full rounded-lg border border-input bg-card px-3 py-2.5 text-sm outline-none placeholder:text-muted-foreground focus:border-ring focus:ring-2 focus:ring-ring/20"
        aria-label="Destination"
        autoComplete="off"
      />
      {open && debounced.trim().length >= 2 && (
        <ul className="absolute z-[1000] mt-1 max-h-60 w-full overflow-auto rounded-lg border border-border bg-popover py-1 shadow-lg">
          {places.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className="flex w-full items-baseline justify-between px-3 py-2 text-left text-sm hover:bg-accent"
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
            <li className="px-3 py-2 text-sm text-muted-foreground">
              {isFetching ? "Searching…" : "No match — click the map instead"}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
