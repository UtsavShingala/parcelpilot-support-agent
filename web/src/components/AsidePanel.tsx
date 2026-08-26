// A panel in the working column that can fold itself away.
//
// The evidence has to be present -- which tool ran, which clause governed -- but
// it does not have to be shouting. Seven source cards open by default push the
// answer off the screen and make the page read as a debug dump rather than a
// support console.
//
// So each panel carries a one-line summary and opens on click. While a turn is
// still running the Work panel stays open, because then the detail *is* the
// content: there is no answer yet, and watching the tools run is the whole point.

import { useEffect, useState } from "react";

export function AsidePanel({
  title,
  summary,
  children,
  open: forcedOpen,
  defaultOpen = false,
}: {
  title: string;
  summary: string;
  children: React.ReactNode;
  /** While set, the panel is held open and cannot be collapsed. */
  open?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  // When a turn finishes, fold the panel back down -- unless the reader opened it
  // themselves, in which case leaving it open respects what they asked for.
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (forcedOpen === false && !touched) setOpen(false);
  }, [forcedOpen, touched]);

  const isOpen = forcedOpen || open;

  return (
    <section className={`panel ${isOpen ? "panel--open" : ""}`}>
      <button
        className="panel__head"
        disabled={forcedOpen}
        onClick={() => {
          setTouched(true);
          setOpen(!open);
        }}
        type="button"
      >
        <span className="panel__title">{title}</span>
        <span className="panel__summary">{summary}</span>
        {!forcedOpen && <span className="panel__chevron">{isOpen ? "−" : "+"}</span>}
      </button>

      {isOpen && <div className="panel__body">{children}</div>}
    </section>
  );
}
