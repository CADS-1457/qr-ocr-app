// Lecture de la carte d'identité belge via l'agent local (dossier eid-agent).
(function () {
  const DEFAULT_URL = "http://127.0.0.1:8765";

  async function read() {
    const url = localStorage.getItem("eidAgentUrl") || DEFAULT_URL;
    let response;
    try {
      response = await fetch(`${url}/card`, { cache: "no-store" });
    } catch {
      throw new Error("Agent eID injoignable : vérifiez qu'il est lancé sur ce poste (dossier eid-agent).");
    }
    const card = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(card.detail || "Lecture impossible.");
    return card;
  }

  window.EidReader = { read };
})();
