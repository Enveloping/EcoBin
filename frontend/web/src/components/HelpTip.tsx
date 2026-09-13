import { useState, type ReactNode } from 'react';
import { InfoCircleOutlined } from '@ant-design/icons';
import { Popover } from 'antd';

/** Optional help stays next to its control and is available without a mouse. */
export default function HelpTip({ label, children }: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger={['hover', 'focus', 'click']}
      content={<div className="context-help-content" onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          setOpen(false);
        }
      }}>{children}</div>}
    >
      <button
        type="button"
        className="context-help-trigger"
        aria-label={`${label}说明`}
        aria-expanded={open}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            setOpen(false);
          }
        }}
      >
        <InfoCircleOutlined />
      </button>
    </Popover>
  );
}
