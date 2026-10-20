import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

export function StockSearchInput({ value, onValueChange }: { value: string; onValueChange: (value: string) => void }) {
  const [draft, setDraft] = useState(value);
  const composing = useRef(false);
  const committed = useRef(value);
  const location = useLocation();
  const navigationType = useNavigationType();

  useEffect(() => {
    // Search updates use REPLACE and can arrive behind the next keystroke.
    // Restore URL text only for external navigation, including Back/Forward.
    if (navigationType !== "REPLACE") {
      composing.current = false;
      committed.current = value;
      setDraft(value);
    }
  }, [location.key, navigationType, value]);

  const commit = (next: string) => {
    if (next === committed.current) return;
    committed.current = next;
    onValueChange(next);
  };

  return (
    <input
      className="ui-input"
      type="search"
      placeholder="輸入股號或名稱"
      value={draft}
      onCompositionStart={() => {
        composing.current = true;
      }}
      onCompositionEnd={(event) => {
        composing.current = false;
        const next = event.currentTarget.value;
        setDraft(next);
        commit(next);
      }}
      onChange={(event) => {
        const next = event.currentTarget.value;
        setDraft(next);
        // URL navigation must not replace the browser's active IME preedit.
        if (!composing.current) commit(next);
      }}
    />
  );
}
