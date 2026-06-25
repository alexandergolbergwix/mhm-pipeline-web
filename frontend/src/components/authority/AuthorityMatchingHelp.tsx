/** How authority matching works — curator-facing help (Rule W-33). */
import {Glass} from "@/components/glass";
export function AuthorityMatchingHelp() {
  return (
    <Glass as="details" className="rounded-lg p-4 text-sm space-y-2">
      <summary className="cursor-pointer font-medium text-ink select-none">
        How matching works (Mazal · VIAF · KIMA · Wikidata)
      </summary>
      <div className="muted space-y-2 pt-2 leading-relaxed">
        <p>
          Each row is one entity extracted from the MARC record (author, place, former owner,
          work title from notes, institution, etc.). The system routes by <strong className="text-ink">entity kind</strong>:
        </p>
        <ul className="list-disc ps-5 space-y-1">
          <li><strong className="text-ink">Places</strong> — KIMA first (coordinates + Wikidata QID), then Mazal place ID, Ashkenazi gazetteer fallback.</li>
          <li><strong className="text-ink">Persons</strong> — Mazal (prefers אישיות tag 100 over נושא 150), then VIAF, then Wikidata. MARC birth/death dates ($d) narrow homonyms.</li>
          <li><strong className="text-ink">Works</strong> — Mazal work headings (from contents / כולל: notes).</li>
          <li><strong className="text-ink">Institutions</strong> — Mazal corporate bodies (MARC 110/610/710). Pipe-separated 710 values (e.g. library|former owner name) are split; personal names like <em>Allony, Nehemia</em> route as persons even on tag 710.</li>
          <li><strong className="text-ink">Topics</strong> — Mazal subject headings (MARC 650).</li>
        </ul>
        <p>
          <strong className="text-ink">Confidence</strong> reflects how many sources agree and name quality
          (length, patronymic, catalog heading form). Guards flag issues — e.g.{" "}
          <code className="text-xs">mazal_subject_not_personality</code> when an author matched a נושא record instead of אישיות, or{" "}
          <code className="text-xs">homonym_unresolved</code> when several Mazal personalities tie without dates — use the drawer <strong className="text-ink">Pick</strong> control.
        </p>
        <p>
          After a system update, run <strong className="text-ink">Re-enrich</strong> to refresh matches
          while keeping your approvals. Use <strong className="text-ink">Search notes</strong> to find entities
          whose manuscript has colophon / הערות text even when the entity itself came from a heading field.
        </p>
        <p>
          <strong className="text-ink">Author + subject duplicates:</strong> the same person may appear once as
          MARC 100 (author) and once as MARC 600 (subject). These stay as two rows because the roles differ;
          enable <strong className="text-ink">Group duplicates</strong> to collapse the view, and check
          <em> Linked author personality</em> on subject rows when the author row resolved to אישיות.
        </p>
        <details className="text-xs">
          <summary className="cursor-pointer font-medium text-ink">איך נקבעת ההתאמה (עברית)</summary>
          <div className="muted space-y-2 pt-2 leading-relaxed" dir="rtl">
            <p>
              לכל ישות במאגר MARC המערכת בוחרת מסלול לפי סוג הישות: מקומות — KIMA ואז מז״ל;
              אנשים — מז״ל (מעדיף אישיות תג 100 על פני נושא 150), VIAF, ויקידאטה;
              יצירות — כותר עבודה במז״ל; מוסדות — גוף תאגידי במז״ל.
            </p>
            <p>
              הומונימים ללא תאריכי $d — המערכת נמנעת מניחוש ומסמנת{" "}
              <code className="text-xs">homonym_unresolved</code>; בחרי אישיות במגירת הפרטים.
            </p>
            <p>
              לאחר עדכון מערכת יש להריץ <strong className="text-ink">Re-enrich</strong>.
              חיפוש בהערות מאפשר למצוא קולופון, «כולל:», «בעריכת» ו«הגהות».
            </p>
          </div>
        </details>
      </div>
    </Glass>
  );
}
