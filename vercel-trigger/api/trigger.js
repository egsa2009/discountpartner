// api/trigger.js v2 — Modo manual (URLs) + modo automático
// Env vars en Vercel: GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, PAGE_PASSWORD

module.exports = async function handler(req, res) {
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  const { password, mode, urls, count, dry_run } = req.body || {};

  if (!process.env.PAGE_PASSWORD || password !== process.env.PAGE_PASSWORD) {
    return res.status(401).json({ error: "Contraseña incorrecta" });
  }

  const owner = process.env.GITHUB_OWNER;
  const repo  = process.env.GITHUB_REPO;
  const token = process.env.GITHUB_TOKEN;

  if (!owner || !repo || !token) {
    return res.status(500).json({ error: "Configuración de GitHub incompleta" });
  }

  try {
    if (mode === "manual") {
      // ── Modo manual: URLs que el usuario pegó ──────────────────────────────
      const urlList = (urls || []).slice(0, 10).filter(u => u && u.includes("amazon"));
      if (urlList.length === 0) {
        return res.status(400).json({ error: "No se encontraron URLs válidas de Amazon" });
      }

      const apiUrl = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/process_urls.yml/dispatches`;
      const response = await fetch(apiUrl, {
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
            urls: urlList.join(","),
          },
        }),
      });

      if (response.status === 204) {
        return res.status(200).json({
          success: true,
          message: `${urlList.length} producto${urlList.length > 1 ? "s" : ""} en proceso → Telegram`,
        });
      }

      const data = await response.json().catch(() => ({}));
      return res.status(response.status).json({
        error: data.message || `GitHub status ${response.status}`,
      });

    } else {
      // ── Modo automático: busca deals en Slickdeals/Reddit ─────────────────
      const apiUrl = `https://api.github.com/repos/${owner}/${repo}/actions/workflows/post.yml/dispatches`;
      const response = await fetch(apiUrl, {
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
            count:   String(count || "10"),
            dry_run: dry_run === true || dry_run === "true" ? "true" : "false",
          },
        }),
      });

      if (response.status === 204) {
        return res.status(200).json({
          success: true,
          message: `Buscando ${count || 10} deals automáticamente`,
        });
      }

      const data = await response.json().catch(() => ({}));
      return res.status(response.status).json({
        error: data.message || `GitHub status ${response.status}`,
      });
    }

  } catch (err) {
    return res.status(500).json({ error: `Error de red: ${err.message}` });
  }
};
