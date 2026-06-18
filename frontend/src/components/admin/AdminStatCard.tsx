import {Link} from "react-router-dom";
import {Glass, GlassPill} from "@/components/glass";

interface Props {
  label: string;
  value: number | string;
  badge?: number;
  href?: string;
}

export function AdminStatCard({label, value, badge, href}: Props) {
  const inner = (
    <Glass className="p-5 rounded-2xl flex flex-col gap-1 hover:bg-white/5 transition">
      <div className="flex items-start justify-between gap-2">
        <span className="text-3xl font-bold text-ink">{value}</span>
        {badge !== undefined && badge > 0 && (
          <GlassPill className="text-xs badge-warn px-2 py-0.5">
            {badge} pending
          </GlassPill>
        )}
      </div>
      <span className="kicker">{label}</span>
    </Glass>
  );

  if (href) {
    return <Link to={href} className="block">{inner}</Link>;
  }
  return inner;
}
