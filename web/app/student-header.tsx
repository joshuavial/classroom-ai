import type { Student } from "@/lib/types";
import styles from "@/app/student.module.css";

/** Shown at the top of every student page (R4.7), so a teacher walking past
 * can check the screen matches the slip and the student. */
export function StudentHeader({ student }: { student: Student }) {
  return (
    <header className={styles.header}>
      <span>
        <strong>{student.name}</strong>
      </span>
      <span>
        Code <strong>{student.code}</strong>
      </span>
      <span>{student.class_name}</span>
    </header>
  );
}
