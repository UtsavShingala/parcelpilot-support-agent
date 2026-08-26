// What an answer rests on.
//
// The tier badge is the point of this component. A customer agreement and a
// superseded policy look identical as prose, and the difference decides the
// answer -- so it is shown as a label rather than left implicit in the filename.

import { useState } from "react";
import type { Citation } from "../types";

const TIER_LABEL: Record<string, string> = {
  AGREEMENT: "Your agreement",
  CURRENT_POLICY: "Current policy",
  PRODUCT_DOC: "Product docs",
  HISTORICAL: "Past ticket",
  DEPRECATED: "Superseded",
};

const TIER_NOTE: Record<string, string> = {
  AGREEMENT: "Overrides general policy for this account",
  CURRENT_POLICY: "Applies to all customers",
  PRODUCT_DOC: "Describes behaviour, does not grant entitlements",
  HISTORICAL: "Context only — may contain incorrect past guidance",
  DEPRECATED: "Withdrawn — cited only to explain what changed",
};

export function SourceCitation({ source }: { source: Citation }) {
  const [open, setOpen] = useState(false);
  const tier = source.authority_tier;

  return (
    <div className={`citation citation--${tier.toLowerCase()}`}>
      <button className="citation__head" onClick={() => setOpen(!open)} type="button">
        <span className={`tier tier--${tier.toLowerCase()}`}>{TIER_LABEL[tier] ?? tier}</span>
        <span className="citation__title">{source.citation}</span>
        <span className="citation__chevron">{open ? "−" : "+"}</span>
      </button>

      <div className="citation__meta">
        <span title="Source file">{source.source_file}</span>
        {source.version && <span title="Document version">{source.version}</span>}
        {source.clause && <span title="Clause">{source.clause}</span>}
        {source.effective_date && (
          <span title="Effective date">eff. {source.effective_date}</span>
        )}
        {source.applies_to !== "global" && (
          <span className="citation__scoped" title="Scope">
            {source.applies_to} only
          </span>
        )}
      </div>

      {open && (
        <>
          <p className="citation__note">{TIER_NOTE[tier]}</p>
          <blockquote className="citation__text">{source.text}</blockquote>
        </>
      )}
    </div>
  );
}
