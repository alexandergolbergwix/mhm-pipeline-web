/**
 * App-wide liquid-glass surfaces — wraps LiquidGlassSurface with layout presets.
 *
 * Use <Glass> for panels/cards/modals and <GlassPill> for chips/badges.
 */

import {
  forwardRef,
  type CSSProperties,
  type ElementType,
  type FormEventHandler,
  type HTMLAttributes,
  type MouseEventHandler,
  type ReactNode,
  type Ref,
} from "react";

import {LiquidGlassSurface} from "@/components/glass/LiquidGlassSurface";


export type GlassVariant = "panel" | "drawer" | "modal" | "pill" | "compact";

type GlassOwnProps = {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  as?: ElementType;
  variant?: GlassVariant;
  contentClassName?: string;
  borderRadius?: number;
  bezelWidth?: number;
  thickness?: number;
};

export type GlassProps = GlassOwnProps &
  Omit<HTMLAttributes<HTMLElement>, keyof GlassOwnProps> & {
    type?: "button" | "submit" | "reset" | string;
    to?: string;
    onSubmit?: FormEventHandler<HTMLFormElement>;
    onClick?: MouseEventHandler<HTMLElement>;
    title?: string;
    role?: string;
    "aria-modal"?: boolean | "true" | "false";
    "aria-labelledby"?: string;
    "aria-pressed"?: boolean;
    "data-testid"?: string;
    "data-active"?: boolean;
    "data-role"?: string;
  };

const VARIANT_PRESETS: Record<GlassVariant, {
  borderRadius: number;
  bezelWidth: number;
  thickness: number;
  shell: string;
  content: string;
}> = {
  panel: {
    borderRadius: 26,
    bezelWidth: 28,
    thickness: 14,
    shell: "glass-shell",
    content: "",
  },
  drawer: {
    borderRadius: 20,
    bezelWidth: 26,
    thickness: 12,
    shell: "glass-shell glass-shell-drawer",
    content: "flex flex-col min-h-0 h-full",
  },
  modal: {
    borderRadius: 24,
    bezelWidth: 30,
    thickness: 14,
    shell: "glass-shell",
    content: "",
  },
  compact: {
    borderRadius: 16,
    bezelWidth: 18,
    thickness: 10,
    shell: "glass-shell glass-shell-compact",
    content: "",
  },
  pill: {
    borderRadius: 999,
    bezelWidth: 10,
    thickness: 5,
    shell: "glass-shell glass-shell-pill glass-pill",
    content: "inline-flex items-center",
  },
};

export const Glass = forwardRef<HTMLElement, GlassProps>(function Glass(
  {
    children,
    className = "",
    style,
    as: Component = "div",
    variant = "panel",
    contentClassName,
    borderRadius,
    bezelWidth,
    thickness,
    ...rest
  },
  ref,
) {
  const preset = VARIANT_PRESETS[variant];
  const shellClass = `${preset.shell} ${className}`.trim();
  const innerClass = contentClassName ?? preset.content;

  return (
    <LiquidGlassSurface
      ref={ref as Ref<HTMLElement>}
      as={Component}
      className={shellClass}
      style={style}
      borderRadius={borderRadius ?? preset.borderRadius}
      bezelWidth={bezelWidth ?? preset.bezelWidth}
      thickness={thickness ?? preset.thickness}
      contentClassName={innerClass}
      {...rest}
    >
      {children}
    </LiquidGlassSurface>
  );
});

export function GlassPill({
  children,
  className = "",
  as: Component = "span",
  ...rest
}: Omit<GlassProps, "variant">) {
  return (
    <Glass as={Component} variant="pill" className={className} {...rest}>
      {children}
    </Glass>
  );
}

Glass.displayName = "Glass";
