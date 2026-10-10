import type { LessonSession } from "@/lib/types";
import styles from "@/app/teach/slips/slips.module.css";

/** A4 sheet of cut-out slips, one per unused code (R3.5). Print CSS only. */
export function Slips({ session, address }: { session: LessonSession; address: string }) {
  const unused = session.roster.filter((r) => !r.name);
  return (
    <main className={styles.sheet}>
      <div className={styles.controls}>
        <h1>Slips for {session.class_name}</h1>
        <p>
          {unused.length} unused codes. Cut along the dashed lines. <button type="button" onClick={() => window.print()}>Print</button>
        </p>
      </div>
      <div className={styles.grid}>
        {unused.map((r) => (
          <div key={r.id} className={styles.slip} data-testid="slip">
            <span className={styles.small}>{session.class_name}</span>
            <span className={styles.code}>{r.code}</span>
            <span className={styles.small}>Go to {address} and type this code and your name.</span>
          </div>
        ))}
      </div>
    </main>
  );
}

