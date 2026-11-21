import {useSearchParams} from "react-router-dom";

import {type ReferenceScope} from "@/api/referenceRepairs";
import {AdminLayout} from "@/components/admin/AdminLayout";
import {ReferenceRepairQueue} from "@/components/wikidata/ReferenceRepairQueue";
import {useReferenceRepairStatus} from "@/hooks/useReferenceRepairStatus";

export default function AdminReferenceRepairs() {
  const [params, setParams] = useSearchParams();
  const scope = params.get("scope") === "remaining" ? "remaining" : "original";
  return <AdminLayout>
    <nav aria-label="Reference repair scope" className="mb-4 flex flex-wrap gap-2">
      <button className={scope === "original" ? "button-primary" : "button-ghost"} aria-pressed={scope === "original"}
        onClick={() => setParams({})}>Original queue</button>
      <button className={scope === "remaining" ? "button-primary" : "button-ghost"} aria-pressed={scope === "remaining"}
        onClick={() => setParams({scope: "remaining"})}>Remaining references</button>
    </nav>
    <ReferenceRepairScope key={scope} scope={scope} />
  </AdminLayout>;
}

function ReferenceRepairScope({scope}: {scope: ReferenceScope}) {
  const status = useReferenceRepairStatus(scope);
  return <ReferenceRepairQueue scope={scope} status={status} />;
}
