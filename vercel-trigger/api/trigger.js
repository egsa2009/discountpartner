// api/trigger.js — Serverless function que dispara el workflow de GitHub Actions
// Variables de entorno requeridas en Vercel:
//   GITHUB_TOKEN   — Personal Access Token con scope "workflow"
//   GITHUB_OWNER   — tu usuario de GitHub (ej: erikgsa)
//   GITHUB_REPO    — nombre del repositorio (ej: discountpartner)
//   PAGE_PASSWORD  — contraseña para proteger esta página

export default async function handler(req, res) {
  // Solo POST
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  // Verificar contraseña
  const { password, count, dry_run } = req.body || {};
  const correctPassword = process.env.PAGE_PASSWORD;

  if (!correctPassword || password !== correctPassword) {
    return res.status(401).json({ error: "Contraseña incorrecta" });
  }

  // Parámetros del workflow
  const owner  = process.env.GITHUB_OWNER;
  const repo   = process.env.GITHUB_REPO;
  const token  = process.env.GITHUB_TOKEN;

  if (!owner || !repo || !token) {
    return res.status(500).json({ error: "Configuración de GitHub incompleta" });
  }

  const url = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/post.yml/dispatches`;

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "main",
        inputs: {
          count: String(count || "10"),
          dry_run: dry_run === true || dry_run === "true" ? "true" : "false",
        },
      }),
    });

    if (response.status === 204) {
      return res.status(200).json({
        success: true,
        message: `Pipeline iniciado con ${count || 10} deals`,
      });
    }

    const data = await response.json().catch(() => ({}));
    return res.status(response.status).json({
      error: data.message || `GitHub respondió con ${response.status}`,
    });
  } catch (err) {
    return res.status(500).json({ error: `Error de red: ${err.message}` });
  }
}
