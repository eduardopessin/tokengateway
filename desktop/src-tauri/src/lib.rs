use std::sync::Arc;
use serde_json::Value;
use tauri::{command, AppHandle, State, WebviewUrl, WebviewWindowBuilder};

pub mod oauth;
use oauth::*;

pub fn open_browser(url: &str) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        use windows::Win32::UI::Shell::ShellExecuteW;
        use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
        use windows::core::HSTRING;
        let op = HSTRING::from("open");
        let target = HSTRING::from(url);
        unsafe {
            let res = ShellExecuteW(None, &op, &target, None, None, SW_SHOWNORMAL);
            if res.0 as usize <= 32 {
                let _ = webbrowser::open(url);
            }
        }
        Ok(())
    }
    #[cfg(not(target_os = "windows"))]
    {
        webbrowser::open(url).map_err(|e| e.to_string())
    }
}

#[command]
async fn start_login(provider: String, state: State<'_, Arc<OAuthState>>) -> Result<String, String> {
    let (verifier, challenge) = generate_pkce();
    let state_uuid = uuid_v4_simple();

    let auth_url = match provider.as_str() {
        "anthropic" => {
            let mut v = state.verifiers.lock().await;
            v.insert("anthropic".to_string(), verifier);
            let redirect_uri = format!("http://localhost:{}{}", ANTHROPIC_PORT, ANTHROPIC_CALLBACK_PATH);
            format!(
                "{}?code=true&client_id={}&response_type=code&redirect_uri={}&scope={}&code_challenge={}&code_challenge_method=S256&state={}",
                ANTHROPIC_AUTH_URL,
                ANTHROPIC_CLIENT_ID,
                urlencoding::encode(&redirect_uri),
                urlencoding::encode(ANTHROPIC_SCOPES),
                challenge,
                state_uuid
            )
        }
        "openai-codex" => {
            let mut v = state.verifiers.lock().await;
            v.insert("openai-codex".to_string(), verifier);
            let redirect_uri = format!("http://localhost:{}{}", OPENAI_PORT, OPENAI_CALLBACK_PATH);
            format!(
                "{}?response_type=code&client_id={}&redirect_uri={}&scope={}&code_challenge={}&code_challenge_method=S256&state={}&id_token_add_organizations=true&codex_cli_simplified_flow=true&originator=pi",
                OPENAI_AUTH_URL,
                OPENAI_CLIENT_ID,
                urlencoding::encode(&redirect_uri),
                urlencoding::encode(OPENAI_SCOPES),
                challenge,
                state_uuid
            )
        }
        "google-antigravity" => {
            let redirect_uri = format!("http://localhost:{}{}", GOOGLE_PORT, GOOGLE_CALLBACK_PATH);
            format!(
                "{}?client_id={}&response_type=code&redirect_uri={}&scope={}&state={}&access_type=offline&prompt=consent",
                GOOGLE_AUTH_URL,
                GOOGLE_CLIENT_ID,
                urlencoding::encode(&redirect_uri),
                urlencoding::encode(GOOGLE_SCOPES),
                state_uuid
            )
        }
        _ => return Err(format!("Provedor desconhecido: {}", provider)),
    };

    open_browser(&auth_url)?;

    Ok(auth_url)
}

fn uuid_v4_simple() -> String {
    use rand::RngCore;
    let mut bytes = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex::encode(bytes)
}

#[command]
fn open_url(url: String) -> Result<(), String> {
    open_browser(&url)
}

#[command]
async fn paste_redirect(
    provider: String,
    url_or_code: String,
    state: State<'_, Arc<OAuthState>>,
    app: AppHandle,
) -> Result<OAuthCredential, String> {
    let code = if url_or_code.contains("code=") {
        url_or_code
            .split("code=")
            .nth(1)
            .and_then(|s| s.split('&').next())
            .unwrap_or(&url_or_code)
    } else {
        &url_or_code
    };

    let state_param = if url_or_code.contains("state=") {
        url_or_code
            .split("state=")
            .nth(1)
            .and_then(|s| s.split('&').next())
    } else {
        None
    };

    handle_token_exchange(&provider, code, state_param, &state, &app).await
}

#[command]
async fn get_status(state: State<'_, Arc<OAuthState>>) -> Result<Vec<ProviderStatus>, String> {
    let creds = state.credentials.lock().await;
    let now = now_ms();

    let list = vec![
        ProviderStatus {
            id: "anthropic".to_string(),
            label: "Anthropic (Claude Max)".to_string(),
            port: ANTHROPIC_PORT,
            listening: true,
            connected: creds.get("anthropic").map_or(false, |c| c.expires > now),
            email: creds.get("anthropic").and_then(|c| c.email.clone()),
            expires_at: creds.get("anthropic").map(|c| c.expires),
        },
        ProviderStatus {
            id: "openai-codex".to_string(),
            label: "OpenAI (ChatGPT Plus / Codex)".to_string(),
            port: OPENAI_PORT,
            listening: true,
            connected: creds.get("openai-codex").map_or(false, |c| c.expires > now),
            email: creds.get("openai-codex").and_then(|c| c.email.clone()),
            expires_at: creds.get("openai-codex").map(|c| c.expires),
        },
        ProviderStatus {
            id: "google-antigravity".to_string(),
            label: "Google Antigravity".to_string(),
            port: GOOGLE_PORT,
            listening: true,
            connected: creds.get("google-antigravity").map_or(false, |c| c.expires > now),
            email: creds.get("google-antigravity").and_then(|c| c.email.clone()),
            expires_at: creds.get("google-antigravity").map(|c| c.expires),
        },
    ];

    Ok(list)
}

#[command]
async fn get_cluster_usage(
    cluster_url: Option<String>,
    state: State<'_, Arc<OAuthState>>,
) -> Result<Value, String> {
    let url = cluster_url.unwrap_or_else(|| CLUSTER_USAGE_URL.to_string());
    let resp = state
        .http_client
        .get(&url)
        .send()
        .await
        .map_err(|e| format!("Falha ao contactar cluster: {}", e))?;

    if resp.status().is_success() {
        let val: Value = resp.json().await.map_err(|e| format!("JSON inválido: {}", e))?;
        Ok(val)
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

#[command]
async fn sync_to_cluster(
    cluster_url: Option<String>,
    state: State<'_, Arc<OAuthState>>,
) -> Result<String, String> {
    let creds = state.credentials.lock().await;
    if creds.is_empty() {
        return Err("Nenhuma credencial local para sincronizar.".to_string());
    }

    let url = cluster_url.unwrap_or_else(|| CLUSTER_CREDENTIALS_URL.to_string());
    let payload = serde_json::to_value(&*creds).map_err(|e| e.to_string())?;

    let res = state
        .http_client
        .post(&url)
        .json(&payload)
        .send()
        .await
        .map_err(|e| format!("Falha ao contactar cluster: {}", e))?;

    if res.status().is_success() {
        Ok(format!("Sincronizadas {} credenciais com o cluster com sucesso!", creds.len()))
    } else {
        Err(format!("Cluster respondeu com status HTTP {}", res.status()))
    }
}

pub fn run() {
    let oauth_state = Arc::new(OAuthState::new());
    let state_for_listeners = oauth_state.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(oauth_state)
        .invoke_handler(tauri::generate_handler![
            start_login,
            open_url,
            paste_redirect,
            get_status,
            get_cluster_usage,
            sync_to_cluster
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            start_listeners(handle, state_for_listeners);

            let main_win = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::App("index.html".into()),
            )
            .title("Quota Desktop — OAuth Loopback Bridge")
            .inner_size(980.0, 740.0)
            .min_inner_size(800.0, 550.0)
            .resizable(true)
            .center()
            .visible(true)
            .build()?;

            main_win.show()?;

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
