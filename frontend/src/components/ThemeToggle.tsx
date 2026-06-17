import {useTheme, type ColorScheme} from "@/stores/theme";

const OPTIONS: {value: ColorScheme; label: string}[] = [
  {value: "light", label: "Light"},
  {value: "dark", label: "Dark"},
];

export function ThemeToggle() {
  const {colorScheme, setColorScheme} = useTheme();

  return (
    <div
      role="radiogroup"
      aria-label="Color scheme"
      className="inline-flex rounded-full border border-[var(--line)] p-0.5 gap-0.5"
    >
      {OPTIONS.map((opt) => {
        const active = colorScheme === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => setColorScheme(opt.value)}
            className={[
              "px-4 py-1.5 rounded-full text-sm transition",
              active ? "bg-[var(--nav-active-bg)] text-ink font-medium" : "muted hover:text-ink",
            ].join(" ")}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
