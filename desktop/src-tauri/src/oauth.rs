use std::collections::HashMap;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::Mutex;

// ── 1. Anthropic Claude Max / Claude Code ──────────────────────────────────────
pub const ANTHROPIC_PORT: u16 = 54545;
pub const ANTHROPIC_CALLBACK_PATH: &str = "/callback";
pub const ANTHROPIC_CLIENT_ID: &str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e";
pub const ANTHROPIC_AUTH_URL: &str = "https://claude.ai/oauth/authorize";
pub const ANTHROPIC_TOKEN_URL: &str = "https://api.anthropic.com/v1/oauth/token";
pub const ANTHROPIC_SCOPES: &str = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload";

// ── 2. OpenAI Codex / ChatGPT Plus ───────────────────────────────────────────
pub const OPENAI_PORT: u16 = 1455;
pub const OPENAI_CALLBACK_PATH: &str = "/auth/callback";
pub const OPENAI_CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
pub const OPENAI_AUTH_URL: &str = "https://auth.openai.com/oauth/authorize";
pub const OPENAI_TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
pub const OPENAI_SCOPES: &str = "openid profile email offline_access api.connectors.read api.connectors.invoke";

// ── 3. Google Antigravity (Cloud Code) ───────────────────────────────────────
pub const GOOGLE_PORT: u16 = 51121;
pub const GOOGLE_CALLBACK_PATH: &str = "/oauth-callback";
pub const GOOGLE_CLIENT_ID: &str = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com";
pub const GOOGLE_CLIENT_SECRET: &str = env!("GOOGLE_CLIENT_SECRET");
pub const GOOGLE_AUTH_URL: &str = "https://accounts.google.com/o/oauth2/v2/auth";
pub const GOOGLE_TOKEN_URL: &str = "https://oauth2.googleapis.com/token";
pub const GOOGLE_SCOPES: &str = "https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/cclog https://www.googleapis.com/auth/experimentsandconfigs";

pub const CLUSTER_CREDENTIALS_URL: &str = "http://localhost:3737/api/credentials";
pub const CLUSTER_USAGE_URL: &str = "http://localhost:3737/api/usage";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OAuthCredential {
    pub access: String,
    pub refresh: String,
    pub expires: u64,
    pub email: Option<String>,
    pub plan: Option<String>,
    pub project_id: Option<String>,
    pub authorized_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderStatus {
    pub id: String,
    pub label: String,
    pub port: u16,
    pub listening: bool,
    pub connected: bool,
    pub email: Option<String>,
    pub expires_at: Option<u64>,
}

pub struct OAuthState {
    pub verifiers: Mutex<HashMap<String, String>>,
    pub credentials: Mutex<HashMap<String, OAuthCredential>>,
    pub http_client: Client,
}

impl OAuthState {
    pub fn new() -> Self {
        Self {
            verifiers: Mutex::new(HashMap::new()),
            credentials: Mutex::new(HashMap::new()),
            http_client: Client::builder()
                .danger_accept_invalid_certs(true)
                .build()
                .unwrap_or_default(),
        }
    }
}

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

pub fn generate_pkce() -> (String, String) {
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    let verifier = URL_SAFE_NO_PAD.encode(bytes);

    let mut hasher = Sha256::new();
    hasher.update(verifier.as_bytes());
    let challenge = URL_SAFE_NO_PAD.encode(hasher.finalize());

    (verifier, challenge)
}

pub fn start_listeners(app: AppHandle, state: Arc<OAuthState>) {
    let s1 = state.clone();
    let a1 = app.clone();
    tauri::async_runtime::spawn(async move {
        listen_port(ANTHROPIC_PORT, "anthropic", a1, s1).await;
    });

    let s2 = state.clone();
    let a2 = app.clone();
    tauri::async_runtime::spawn(async move {
        listen_port(OPENAI_PORT, "openai-codex", a2, s2).await;
    });

    let s3 = state.clone();
    let a3 = app.clone();
    tauri::async_runtime::spawn(async move {
        listen_port(GOOGLE_PORT, "google-antigravity", a3, s3).await;
    });
}

async fn listen_port(port: u16, provider_id: &'static str, app: AppHandle, state: Arc<OAuthState>) {
    let addr_v4 = format!("127.0.0.1:{}", port);
    let listener_v4 = TcpListener::bind(&addr_v4).await;
    let addr_v6 = format!("[::1]:{}", port);
    let listener_v6 = TcpListener::bind(&addr_v6).await;

    if let Ok(l4) = listener_v4 {
        println!("[OAuthListener] Escutando em http://{}", addr_v4);
        let a = app.clone();
        let s = state.clone();
        tauri::async_runtime::spawn(async move {
            handle_incoming_connections(l4, provider_id, a, s).await;
        });
    }
    if let Ok(l6) = listener_v6 {
        println!("[OAuthListener] Escutando em http://{}", addr_v6);
        let a = app.clone();
        let s = state.clone();
        tauri::async_runtime::spawn(async move {
            handle_incoming_connections(l6, provider_id, a, s).await;
        });
    }
}

async fn handle_incoming_connections(listener: TcpListener, provider_id: &'static str, app: AppHandle, state: Arc<OAuthState>) {
    loop {
        if let Ok((mut socket, _)) = listener.accept().await {
            let state_clone = state.clone();
            let app_clone = app.clone();
            tauri::async_runtime::spawn(async move {
                let mut buf = [0u8; 4096];
                if let Ok(n) = socket.read(&mut buf).await {
                    if n == 0 {
                        return;
                    }
                    let req_str = String::from_utf8_lossy(&buf[..n]);
                    let (code, state_param) = parse_code_and_state(&req_str);

                    let mut success = false;
                    let mut error_msg = String::new();
                    let mut user_email = None;

                    if let Some(auth_code) = code {
                        match handle_token_exchange(provider_id, &auth_code, state_param.as_deref(), &state_clone, &app_clone).await {
                            Ok(cred) => {
                                success = true;
                                user_email = cred.email.clone();
                                let _ = app_clone.emit("oauth-success", serde_json::json!({
                                    "provider": provider_id,
                                    "email": cred.email,
                                    "expires": cred.expires,
                                    "authorizedAt": cred.authorized_at
                                }));
                            }
                            Err(e) => {
                                error_msg = e;
                                let _ = app_clone.emit("oauth-error", serde_json::json!({
                                    "provider": provider_id,
                                    "error": error_msg
                                }));
                            }
                        }
                    } else {
                        error_msg = "Nenhum código de autorização detetado no callback".to_string();
                    }

                    let html_body = if success {
                        format!(r#"<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="UTF-8">
  <title>Autenticação Concluída</title>
  <style>
    body {{ background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; align-items: center; justify-content: center; min-height: 90vh; margin: 0; }}
    .card {{ background: #1e293b; padding: 40px 60px; border-radius: 16px; text-align: center; border: 1px solid #334155; }}
    h1 {{ color: #38bdf8; margin: 0 0 12px 0; font-size: 24px; }}
    p {{ color: #94a3b8; font-size: 15px; }}
    .badge {{ display: inline-block; padding: 6px 14px; background: rgba(56, 189, 248, 0.15); color: #38bdf8; border-radius: 9999px; font-size: 13px; font-weight: 500; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>✅ Autenticação Concluída!</h1>
    <p>O token foi capturado e sincronizado com o cluster com sucesso.</p>
    <p><strong>{}</strong></p>
    <div class="badge">Pode fechar este separador do browser.</div>
  </div>
  <script>setTimeout(() => window.close(), 2500);</script>
</body>
</html>"#, user_email.unwrap_or_default())
                    } else {
                        format!(r#"<!DOCTYPE html>
<html lang="pt">
<head>
  <meta charset="UTF-8">
  <title>Erro na Autenticação</title>
  <style>
    body {{ background: #0f172a; color: #f8fafc; font-family: sans-serif; display: flex; align-items: center; justify-content: center; min-height: 90vh; margin: 0; }}
    .card {{ background: #1e293b; padding: 40px 60px; border-radius: 16px; text-align: center; border: 1px solid #ef4444; }}
    h1 {{ color: #ef4444; margin: 0 0 12px 0; font-size: 24px; }}
    p {{ color: #cbd5e1; font-size: 15px; }}
    .err {{ color: #f87171; background: rgba(239, 68, 68, 0.1); padding: 10px; border-radius: 8px; font-family: monospace; font-size: 13px; word-break: break-all; margin: 15px 0; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>❌ Falha na Autenticação</h1>
    <p>O Quota Desktop não conseguiu validar o código de autorização.</p>
    <div class="err">{}</div>
    <p>Copie a URL da barra de endereços e cole no campo manual do Quota Desktop.</p>
  </div>
</body>
</html>"#, error_msg)
                    };

                    let resp_header = format!(
                        "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                        html_body.len(),
                        html_body
                    );

                    let _ = socket.write_all(resp_header.as_bytes()).await;
                    let _ = socket.flush().await;
                }
            });
        }
    }
}

fn parse_code_and_state(req: &str) -> (Option<String>, Option<String>) {
    let first_line = req.lines().next().unwrap_or("");
    let parts: Vec<&str> = first_line.split_whitespace().collect();
    if parts.len() < 2 {
        return (None, None);
    }
    let path = parts[1];
    if let Some(query_idx) = path.find('?') {
        let query = &path[query_idx + 1..];
        let mut code = None;
        let mut state = None;
        for pair in query.split('&') {
            let mut kv = pair.split('=');
            if let (Some(k), Some(v)) = (kv.next(), kv.next()) {
                if k == "code" {
                    code = urlencoding::decode(v).ok().map(|s| s.into_owned());
                } else if k == "state" {
                    state = urlencoding::decode(v).ok().map(|s| s.into_owned());
                }
            }
        }
        (code, state)
    } else {
        (None, None)
    }
}

pub async fn handle_token_exchange(
    provider_id: &str,
    code: &str,
    state_param: Option<&str>,
    state: &OAuthState,
    _app: &AppHandle,
) -> Result<OAuthCredential, String> {
    let verifier = {
        let verifiers = state.verifiers.lock().await;
        verifiers.get(provider_id).cloned().unwrap_or_default()
    };

    let cred = match provider_id {
        "anthropic" => exchange_anthropic(code, state_param, &verifier, &state.http_client).await?,
        "openai-codex" => exchange_openai(code, &verifier, &state.http_client).await?,
        "google-antigravity" => exchange_google(code, &state.http_client).await?,
        _ => return Err(format!("Provedor desconhecido: {}", provider_id)),
    };

    {
        let mut creds = state.credentials.lock().await;
        creds.insert(provider_id.to_string(), cred.clone());
    }

    // Auto-sync with Quota Dashboard in background
    let client = state.http_client.clone();
    let creds_map = {
        let creds = state.credentials.lock().await;
        creds.clone()
    };
    tauri::async_runtime::spawn(async move {
        let _ = client
            .post(CLUSTER_CREDENTIALS_URL)
            .json(&creds_map)
            .send()
            .await;
    });

    Ok(cred)
}

async fn exchange_anthropic(code: &str, state: Option<&str>, verifier: &str, client: &Client) -> Result<OAuthCredential, String> {
    let mut clean_code = code.to_string();
    let mut state_extracted = state.map(|s| s.to_string());

    if let Some(hash_idx) = clean_code.find('#') {
        let after = clean_code[hash_idx + 1..].to_string();
        clean_code.truncate(hash_idx);
        if !after.is_empty() {
            state_extracted = Some(after);
        }
    }

    let mut body = serde_json::json!({
        "grant_type": "authorization_code",
        "client_id": ANTHROPIC_CLIENT_ID,
        "code": clean_code,
        "redirect_uri": format!("http://localhost:{}{}", ANTHROPIC_PORT, ANTHROPIC_CALLBACK_PATH),
        "code_verifier": verifier
    });

    if let Some(st) = state_extracted {
        body["state"] = serde_json::Value::String(st);
    }

    let resp = client
        .post(ANTHROPIC_TOKEN_URL)
        .header("content-type", "application/json")
        .header("accept", "application/json")
        .header("anthropic-beta", "oauth-2025-04-20")
        .header("User-Agent", "anthropic-sdk-typescript/0.94.0 userOAuthProvider")
        .json(&body)
        .send()
        .await
        .map_err(|e| format!("Falha no pedido HTTP Anthropic: {}", e))?;

    let res_json: Value = resp.json().await.map_err(|e| format!("Falha ao ler JSON Anthropic: {}", e))?;

    let access = res_json["access_token"].as_str().ok_or("Missing access_token")?.to_string();
    let refresh = res_json["refresh_token"].as_str().unwrap_or("").to_string();
    let expires_in = res_json["expires_in"].as_u64().unwrap_or(28800);
    let email = res_json["oauth_account"]["account_email"].as_str().map(|s| s.to_string());

    Ok(OAuthCredential {
        access,
        refresh,
        expires: now_ms() + (expires_in * 1000),
        email,
        plan: Some("claude-max".to_string()),
        project_id: None,
        authorized_at: now_ms(),
    })
}

async fn exchange_openai(code: &str, verifier: &str, client: &Client) -> Result<OAuthCredential, String> {
    let mut clean_code = code.to_string();
    if let Some(hash_idx) = clean_code.find('#') {
        clean_code.truncate(hash_idx);
    }

    let params = [
        ("grant_type", "authorization_code"),
        ("client_id", OPENAI_CLIENT_ID),
        ("code", clean_code.as_str()),
        ("redirect_uri", "http://localhost:1455/auth/callback"),
        ("code_verifier", verifier),
    ];

    let resp = client
        .post(OPENAI_TOKEN_URL)
        .header("accept", "application/json")
        .form(&params)
        .send()
        .await
        .map_err(|e| format!("Falha no pedido HTTP OpenAI: {}", e))?;

    let status = resp.status();
    let res_text = resp.text().await.map_err(|e| format!("Falha ao ler resposta OpenAI: {}", e))?;
    let res_json: Value = serde_json::from_str(&res_text)
        .map_err(|_| format!("Resposta OpenAI inválida (HTTP {}): {}", status, res_text))?;

    if !status.is_success() {
        return Err(format!("OpenAI recusou troca (HTTP {}): {}", status, res_text));
    }

    let access = res_json["access_token"]
        .as_str()
        .ok_or_else(|| format!("Resposta sem access_token: {}", res_text))?
        .to_string();
    let refresh = res_json["refresh_token"].as_str().unwrap_or("").to_string();
    let expires_in = res_json["expires_in"].as_u64().unwrap_or(864000);

    let (account_id, email, plan_type) = parse_openai_jwt(&access);

    Ok(OAuthCredential {
        access,
        refresh,
        expires: now_ms() + (expires_in * 1000),
        email,
        plan: plan_type.or(Some("plus".to_string())),
        project_id: account_id,
        authorized_at: now_ms(),
    })
}

fn parse_openai_jwt(token: &str) -> (Option<String>, Option<String>, Option<String>) {
    let parts: Vec<&str> = token.split('.').collect();
    if parts.len() < 2 {
        return (None, None, None);
    }
    if let Ok(decoded) = URL_SAFE_NO_PAD.decode(parts[1].as_bytes()) {
        if let Ok(val) = serde_json::from_slice::<Value>(&decoded) {
            let auth_data = &val["https://api.openai.com/auth"];
            let profile_data = &val["https://api.openai.com/profile"];
            let account_id = auth_data["chatgpt_account_id"].as_str().map(|s| s.to_string());
            let email = profile_data["email"].as_str().map(|s| s.to_string());
            let plan = auth_data["chatgpt_plan_type"].as_str().map(|s| s.to_string());
            return (account_id, email, plan);
        }
    }
    (None, None, None)
}

async fn exchange_google(code: &str, client: &Client) -> Result<OAuthCredential, String> {
    let mut clean_code = code.to_string();
    if let Some(hash_idx) = clean_code.find('#') {
        clean_code.truncate(hash_idx);
    }

    let redirect = format!("http://localhost:{}{}", GOOGLE_PORT, GOOGLE_CALLBACK_PATH);
    let params = [
        ("grant_type", "authorization_code"),
        ("client_id", GOOGLE_CLIENT_ID),
        ("client_secret", GOOGLE_CLIENT_SECRET),
        ("code", clean_code.as_str()),
        ("redirect_uri", redirect.as_str()),
    ];

    let resp = client
        .post(GOOGLE_TOKEN_URL)
        .header("accept", "application/json")
        .form(&params)
        .send()
        .await
        .map_err(|e| format!("Falha no pedido Google: {}", e))?;

    let status = resp.status();
    let res_text = resp.text().await.map_err(|e| format!("Falha ao ler resposta Google: {}", e))?;
    let res_json: Value = serde_json::from_str(&res_text)
        .map_err(|_| format!("Resposta Google inválida (HTTP {}): {}", status, res_text))?;

    if !status.is_success() {
        return Err(format!("Google recusou troca (HTTP {}): {}", status, res_text));
    }

    let access = res_json["access_token"].as_str().ok_or("Missing access_token")?.to_string();
    let refresh = res_json["refresh_token"].as_str().unwrap_or("").to_string();
    let expires_in = res_json["expires_in"].as_u64().unwrap_or(3600);

    Ok(OAuthCredential {
        access,
        refresh,
        expires: now_ms() + (expires_in * 1000),
        email: None,
        plan: None,
        project_id: Some("quixotic-airlock-p7k72".to_string()),
        authorized_at: now_ms(),
    })
}
