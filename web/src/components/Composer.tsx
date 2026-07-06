import { memo, useEffect, useRef, useState } from "react";

type ComposerProps = {
  disabled: boolean;
  draft?: string;
  onDraftApplied?: () => void;
  onSubmit: (text: string) => Promise<void> | void;
};

export const Composer = memo(function Composer({
  disabled,
  draft,
  onDraftApplied,
  onSubmit,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (!draft) {
      return;
    }
    setValue(draft);
    onDraftApplied?.();
    textareaRef.current?.focus();
  }, [draft, onDraftApplied]);

  const submit = async () => {
    const nextValue = value.trim();
    if (!nextValue) {
      return;
    }
    await onSubmit(nextValue);
    setValue("");
  };

  const resizeTextarea = () => {
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 160)}px`;
  };

  useEffect(() => {
    resizeTextarea();
  }, [value]);

  return (
    <div className="composer-wrap">
      <form
        className="composer"
        onSubmit={async (event) => {
          event.preventDefault();
          await submit();
        }}
      >
        <div className="composer-input-shell">
          <textarea
            ref={textareaRef}
            value={value}
            disabled={disabled}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={async (event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                await submit();
              }
            }}
            rows={1}
            placeholder="想问就输入"
          />
          <button
            type="submit"
            className="composer-send"
            disabled={disabled || !value.trim()}
            aria-label="发送"
          >
            ↑
          </button>
        </div>
      </form>
    </div>
  );
});
