import styles from "@/app/forms.module.css";

export function ServerDown() {
  return (
    <main className={styles.page}>
      <h1>The server is not answering</h1>
      <p role="alert">Try again in a moment. If it keeps happening, ask the person who runs the server.</p>
    </main>
  );
}
