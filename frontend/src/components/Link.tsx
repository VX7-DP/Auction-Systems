import type { AnchorHTMLAttributes, MouseEvent } from 'react'
import { navigate } from '../lib/router.ts'

type Props = AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }

/** An <a> that navigates in-app, but still opens new tabs on ctrl/cmd/middle click. */
export function Link({ to, onClick, ...rest }: Props) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event)
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey ||
      rest.target === '_blank'
    ) {
      return
    }
    event.preventDefault()
    navigate(to)
  }
  return <a href={to} onClick={handleClick} {...rest} />
}
