import { useLayoutEffect, useRef, type ReactNode } from 'react';

// Native modal dialog: top layer escapes parent transforms; Escape, focus trap, backdrop built in.
export function Drawer({ label, onClose, children }: { label: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const opener = document.activeElement as HTMLElement | null;
    dialog.showModal();
    return () => {
      dialog.close();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-label={label}
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onClick={(event) => event.target === event.currentTarget && onClose()}
      className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-[min(560px,100vw)] max-w-none overflow-y-auto overscroll-contain border-0 border-l border-border-active bg-surface-1 p-0 text-text-secondary shadow-2xl backdrop:bg-black/65 backdrop:backdrop-blur-sm animate-in slide-in-from-right duration-300 motion-reduce:animate-none"
    >
      <div className="relative min-h-full p-5">{children}</div>
    </dialog>
  );
}
