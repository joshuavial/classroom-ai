"use client";

import { useState } from "react";
import { apiBlob, saveBlob } from "@/lib/api";
import { onAuthError } from "@/lib/session";

export default function RootCertificate() {
  const [message, setMessage] = useState("");

  async function download() {
    try {
      const { blob, filename } = await apiBlob("/admin/root-certificate");
      saveBlob(blob, filename);
      setMessage("Root certificate downloaded.");
    } catch (error) {
      onAuthError(error);
      setMessage("Could not download the root certificate. Check that the server is running, then try again.");
    }
  }

  return (
    <section aria-labelledby="root-certificate-heading">
      <h2 id="root-certificate-heading">Root certificate</h2>
      <p>Download this certificate to trust the server on school devices.</p>
      <p role="status">{message}</p>
      <button type="button" onClick={download}>Download root certificate</button>
    </section>
  );
}
