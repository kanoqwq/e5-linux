import * as React from 'react';
import * as Primitive from '@radix-ui/react-dialog';
import {X} from 'lucide-react';
import {cn} from '../../utils';

export const Dialog = Primitive.Root;
export const DialogTitle = Primitive.Title;
export const DialogDescription = Primitive.Description;

export function DialogContent({children, className, closeLabel, ...props}:
  React.ComponentPropsWithoutRef<typeof Primitive.Content> & {closeLabel: string}) {
  const previousFocus = React.useRef<HTMLElement | null>(null);
  return <Primitive.Portal>
    <Primitive.Overlay className="dialog-overlay"/>
    <Primitive.Content className={cn('dialog-content', className)}
      onOpenAutoFocus={event => {
        previousFocus.current = document.activeElement as HTMLElement;
        if (/Android/i.test(navigator.userAgent)) {
          event.preventDefault();
          requestAnimationFrame(() => Array.from(document.querySelectorAll<HTMLElement>('[role="dialog"][data-state="open"]')).at(-1)?.focus({preventScroll:true}));
        }
      }}
      onCloseAutoFocus={event => {
        event.preventDefault();
        if (!document.querySelector('[role="dialog"]') && previousFocus.current?.isConnected)
          previousFocus.current.focus({preventScroll:true});
      }} {...props}>
      {children}
      <Primitive.Close className="dialog-close" aria-label={closeLabel}><X size={18}/></Primitive.Close>
    </Primitive.Content>
  </Primitive.Portal>;
}
