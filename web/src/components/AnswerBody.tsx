// The answer, rendered rather than printed.
//
// Models format their answers in markdown, and the difference between a support
// answer and a wall of asterisks is entirely in whether anyone renders it. Headings
// separate "am I eligible" from "how much"; bold marks the figures that matter.
//
// Raw HTML is deliberately not enabled. The text being rendered comes from a model
// reasoning over retrieved documents, and neither of those is a source I would hand
// unrestricted markup rights to a page.

import Markdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

export function AnswerBody({ text }: { text: string }) {
  return (
    <div className="answer">
      <Markdown
        remarkPlugins={[
          remarkGfm,
          // Single newlines become line breaks. Markdown normally folds them into
          // one paragraph, which would flatten scripted mode's line-per-record
          // output into an unreadable run-on.
          remarkBreaks,
        ]}
      >
        {text}
      </Markdown>
    </div>
  );
}
