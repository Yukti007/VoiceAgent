import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

const base: IconProps = {
  width: 20,
  height: 20,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.9,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  "aria-hidden": true,
};

export function MicIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0" />
      <path d="M12 17.5V21" />
    </svg>
  );
}

export function PhoneDownIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M3.2 13.6c4.9-4.2 12.7-4.2 17.6 0 .6.5.7 1.4.2 2l-1.5 1.8c-.4.5-1.2.6-1.8.3l-2.6-1.3a1.4 1.4 0 0 1-.8-1.4l.1-1.4a10 10 0 0 0-4.8 0l.1 1.4c0 .6-.3 1.1-.8 1.4l-2.6 1.3c-.6.3-1.4.2-1.8-.3L3 15.6c-.5-.6-.4-1.5.2-2Z" />
    </svg>
  );
}

export function ChatIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M20 11.5a7.5 7.5 0 0 1-11 6.6L4 19.5l1.4-4.6A7.5 7.5 0 1 1 20 11.5Z" />
      <path d="M9 10h6M9 13h4" />
    </svg>
  );
}

export function CloseIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

export function ChevronIcon(props: IconProps) {
  return (
    <svg {...base} {...props}>
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

/** WhatsApp-style double tick; `read` switches it to the "seen" tint via CSS. */
export function TicksIcon({ read, ...props }: IconProps & { read?: boolean }) {
  return (
    <svg
      width={16}
      height={11}
      viewBox="0 0 16 11"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={read ? "ticks read" : "ticks"}
      {...props}
    >
      <path d="M1 6l3 3 6-7.5" />
      <path d="M7.5 8.4 8.2 9l6-7.5" />
    </svg>
  );
}
