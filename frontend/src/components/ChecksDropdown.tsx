import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";

/** Native top-layer popover also works inside the Library's scrolling table. */
export default function ChecksDropdown({ label, trigger, children, className = "" }: {
  label: string; trigger: ReactNode; children: ReactNode; className?: string;
}) {
  const id = useId();
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 12, top: 12, width: 390, maxHeight: 500 });
  const place = useCallback(() => {
    const rect = button.current?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(390, window.innerWidth - 24);
    const below = window.innerHeight - rect.bottom - 16;
    const above = rect.top - 16;
    const flip = below < 280 && above > below;
    const maxHeight = Math.max(120, Math.min(540, flip ? above : below));
    const height = Math.min(panel.current?.scrollHeight || 540, maxHeight);
    setPosition({ width, maxHeight,
      left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
      top: flip ? Math.max(12, rect.top - height - 8) : rect.bottom + 8,
    });
  }, []);
  useEffect(() => {
    if (!open) return;
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);
  return <div className={`checks-dropdown ${className}`} onClick={(event) => event.stopPropagation()}>
    <button ref={button} className="checks-trigger" type="button" popoverTarget={id} onClick={place}
      aria-label={label} aria-expanded={open} aria-controls={id} title={label}>
      {trigger}<span className="checks-chevron" aria-hidden="true">⌄</span>
    </button>
    <div ref={panel} id={id} popover="auto" className="checks-popover" role="region" aria-label={label}
      style={{ ...position, inset: "auto", left: position.left, top: position.top }}
      onToggle={(event) => setOpen(event.newState === "open")}>
      {children}
    </div>
  </div>;
}
