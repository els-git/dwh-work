import { createRoot } from 'react-dom/client'

import ConfirmDialog from './ConfirmDialog'

export interface ConfirmActionOptions {
  title: string
  message: string
  detail?: string
  confirmLabel?: string
  cancelLabel?: string
}

export function confirmAction(
  options: ConfirmActionOptions,
): Promise<boolean> {
  return new Promise<boolean>(
    resolve => {
      const container =
        document.createElement('div')

      document.body.appendChild(
        container,
      )

      const root =
        createRoot(container)

      function finish(
        result: boolean,
      ) {
        root.unmount()
        container.remove()
        resolve(result)
      }

      root.render(
        <ConfirmDialog
          open
          title={options.title}
          message={options.message}
          detail={options.detail}
          confirmLabel={options.confirmLabel}
          cancelLabel={options.cancelLabel}
          onConfirm={
            () => finish(true)
          }
          onCancel={
            () => finish(false)
          }
        />,
      )
    },
  )
}
