/**
 * Liquid-glass panel — SVG feDisplacementMap refraction of the backdrop.
 *
 * Chromium: `backdrop-filter: blur() saturate() url(#filter)` refracts the
 * graph/UI behind the panel (article technique).
 *
 * Safari / Firefox: falls back to enhanced CSS frosted glass (no url() in
 * backdrop-filter).
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useId,
  useState,
  type CSSProperties,
  type ElementType,
  type HTMLAttributes,
  type MutableRefObject,
  type ReactNode,
} from "react";

import {
  buildGlassMaps,
  supportsSvgBackdropFilter,
  type GlassMaps,
} from "@/components/glass/liquidGlassMath";


type LiquidGlassOwnProps = {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  as?: ElementType;
  contentClassName?: string;
  borderRadius?: number;
  bezelWidth?: number;
  thickness?: number;
};

export type LiquidGlassSurfaceProps = LiquidGlassOwnProps &
  Omit<HTMLAttributes<HTMLElement>, keyof LiquidGlassOwnProps>;


export const LiquidGlassSurface = forwardRef<HTMLElement, LiquidGlassSurfaceProps>(
function LiquidGlassSurface({
  children,
  className = "",
  style,
  as: Component = "div",
  contentClassName = "",
  borderRadius = 20,
  bezelWidth = 28,
  thickness = 14,
  ...rest
}, ref) {
  const reactId = useId().replace(/:/g, "");
  const filterId = `liquid-glass-${reactId}`;
  const [rootEl, setRootEl] = useState<HTMLElement | null>(null);
  const [maps, setMaps] = useState<GlassMaps | null>(null);
  const [svgBackdrop, setSvgBackdrop] = useState(false);

  const setRefs = useCallback((node: HTMLElement | null) => {
    setRootEl(node);
    if (typeof ref === "function") ref(node);
    else if (ref) {
      (ref as MutableRefObject<HTMLElement | null>).current = node;
    }
  }, [ref]);

  useEffect(() => {
    setSvgBackdrop(supportsSvgBackdropFilter());
  }, []);

  useEffect(() => {
    const el = rootEl;
    if (!el) return;

    const regen = () => {
      const rect = el.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      if (w < 8 || h < 8) return;
      setMaps(buildGlassMaps({
        width: w,
        height: h,
        radius: borderRadius,
        bezelWidth,
        thickness,
      }));
    };

    regen();
    const ro = new ResizeObserver(regen);
    ro.observe(el);
    return () => ro.disconnect();
  }, [rootEl, borderRadius, bezelWidth, thickness]);

  const backdropStyle: CSSProperties = svgBackdrop && maps?.displacementUrl
    ? {
        backdropFilter: `blur(10px) saturate(165%) url(#${filterId})`,
        WebkitBackdropFilter: `blur(10px) saturate(165%) url(#${filterId})`,
      }
    : {
        backdropFilter: "blur(22px) saturate(180%)",
        WebkitBackdropFilter: "blur(22px) saturate(180%)",
      };

  const isFlexColumn = /\bflex-col\b/.test(className);
  const innerBase = isFlexColumn
    ? "absolute inset-0 z-10 flex flex-col min-h-0 overflow-hidden"
    : "relative z-10";
  const innerClass = contentClassName
    ? `${innerBase} ${contentClassName}`.trim()
    : innerBase;

  const isPositioned = /\b(absolute|fixed|sticky)\b/.test(className);
  const rootPosition = isPositioned ? "" : "relative";
  const hasOverflow = /\boverflow-/.test(className);
  const rootOverflow = hasOverflow ? "" : "overflow-hidden";
  const rootClass = `${rootPosition} ${rootOverflow} ${className}`.trim();

  return (
    <Component
      ref={setRefs}
      className={rootClass}
      style={{borderRadius, ...style}}
      {...rest}
    >
      {svgBackdrop && maps?.displacementUrl && (
        <svg
          aria-hidden
          className="pointer-events-none absolute w-0 h-0"
          colorInterpolationFilters="sRGB"
        >
          <defs>
            <filter
              id={filterId}
              x="-15%"
              y="-15%"
              width="130%"
              height="130%"
              filterUnits="objectBoundingBox"
            >
              <feImage
                href={maps.displacementUrl}
                x="0"
                y="0"
                width="100%"
                height="100%"
                preserveAspectRatio="none"
                result="displacement_map"
              />
              <feDisplacementMap
                in="SourceGraphic"
                in2="displacement_map"
                scale={maps.scale}
                xChannelSelector="R"
                yChannelSelector="G"
                result="refracted"
              />
              {maps.specularUrl && (
                <>
                  <feImage
                    href={maps.specularUrl}
                    x="0"
                    y="0"
                    width="100%"
                    height="100%"
                    preserveAspectRatio="none"
                    result="specular_map"
                  />
                  <feBlend in="refracted" in2="specular_map" mode="screen" />
                </>
              )}
            </filter>
          </defs>
        </svg>
      )}

      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 liquid-glass-backdrop"
        style={{
          ...backdropStyle,
          borderRadius,
        }}
      />

      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 liquid-glass-rim"
        style={{
          borderRadius,
          background: "var(--glass-rim)",
        }}
      />

      <div className={innerClass}>
        {children}
      </div>
    </Component>
  );
});
LiquidGlassSurface.displayName = "LiquidGlassSurface";
