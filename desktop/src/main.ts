export {};

interface TauriInvoke {
  invoke: <T = unknown>(cmd: string, args?: Record<string, unknown>) => Promise<T>;
}

interface TauriEvent {
  listen: <T = unknown>(event: string, handler: (event: { payload: T }) => void) => Promise<() => void>;
}

interface WindowTauri {
  core?: TauriInvoke;
  event?: TauriEvent;
}

interface ProviderStatus {
  id: string;
  label: string;
  port: number;
  listening: boolean;
  connected: boolean;
  email?: string | null;
  expires_at?: number | null;
}

interface OAuthSuccessPayload {
  provider: string;
  email?: string | null;
  expires?: number;
  authorizedAt?: number;
}

declare global {
  interface Window {
    __TAURI__?: WindowTauri;
    startLogin: (provider: string) => Promise<void>;
    pasteRedirect: (provider: string) => Promise<void>;
    syncCluster: () => Promise<void>;
    openWebDashboard: () => void;
  }
}

const tauriCore: TauriInvoke = window.__TAURI__?.core || {
  invoke: async <T = unknown>(cmd: string, args?: Record<string, unknown>): Promise<T> => {
    console.log(`[Mock Invoke] ${cmd}`, args);
    if (cmd === 'get_status') {
      const mock: ProviderStatus[] = [
        { id: 'anthropic', label: 'Anthropic', port: 54545, listening: true, connected: false, email: null, expires_at: null },
        { id: 'openai-codex', label: 'OpenAI Codex', port: 1455, listening: true, connected: false, email: null, expires_at: null },
        { id: 'google-antigravity', label: 'Google Antigravity', port: 51121, listening: true, connected: false, email: null, expires_at: null },
      ];
      return mock as unknown as T;
    }
    if (cmd === 'start_login') {
      return `https://example.com/oauth?provider=${args?.provider}` as unknown as T;
    }
    return {} as unknown as T;
  }
};

const tauriEvent = window.__TAURI__?.event;

function showToast(msg: string, duration = 3500): void {
  const toast = document.getElementById('toast');
  if (toast) {
    toast.textContent = msg;
    toast.classList.remove('hidden');
    setTimeout(() => {
      toast.classList.add('hidden');
    }, duration);
  }
}

async function refreshStatus(): Promise<void> {
  try {
    const statuses = await tauriCore.invoke<ProviderStatus[]>('get_status');
    for (const p of statuses) {
      const dot = document.getElementById(`dot-${p.id}`);
      const st = document.getElementById(`status-${p.id}`);
      const exp = document.getElementById(`exp-${p.id}`);

      if (dot) {
        if (p.connected) {
          dot.classList.add('active');
        } else {
          dot.classList.remove('active');
        }
      }

      if (st) {
        if (p.connected) {
          st.textContent = p.email ? `Conectado (${p.email})` : 'Conectado ✅';
          st.style.color = '#10b981';
        } else {
          st.textContent = 'Não conectado';
          st.style.color = '#94a3b8';
        }
      }

      if (exp && p.expires_at) {
        const remainingMs = p.expires_at - Date.now();
        if (remainingMs > 0) {
          const hours = Math.floor(remainingMs / (1000 * 60 * 60));
          const mins = Math.floor((remainingMs % (1000 * 60 * 60)) / (1000 * 60));
          exp.textContent = `${hours}h ${mins}m`;
        } else {
          exp.textContent = 'Expirado';
        }
      } else if (exp) {
        exp.textContent = '--';
      }
    }
  } catch (e) {
    console.error('Failed to refresh status:', e);
  }
}

window.startLogin = async function(provider: string): Promise<void> {
  try {
    showToast(`A abrir navegador para ${provider}...`);
    const authUrl = await tauriCore.invoke<string>('start_login', { provider });
    window.open(authUrl, '_blank');
  } catch (e) {
    showToast(`Erro ao iniciar login: ${e}`);
  }
};

window.pasteRedirect = async function(provider: string): Promise<void> {
  const input = document.getElementById(`paste-${provider}`) as HTMLInputElement | null;
  if (!input || !input.value.trim()) {
    showToast('Por favor, cole a URL de callback primeiro.');
    return;
  }
  try {
    showToast('A processar token...');
    await tauriCore.invoke('paste_redirect', {
      provider,
      urlOrCode: input.value.trim()
    });
    input.value = '';
    showToast(`✅ ${provider} autenticado com sucesso!`);
    await refreshStatus();
  } catch (e) {
    showToast(`Erro: ${e}`);
  }
};

window.syncCluster = async function(): Promise<void> {
  try {
    showToast('A sincronizar com o cluster Kubernetes...');
    const res = await tauriCore.invoke<string>('sync_to_cluster', { clusterUrl: null });
    showToast(`✅ ${res}`);
  } catch (e) {
    showToast(`Erro de sincronização: ${e}`);
  }
};

window.openWebDashboard = function(): void {
  window.open('http://localhost:3737', '_blank');
};

if (tauriEvent) {
  tauriEvent.listen<OAuthSuccessPayload>('oauth-success', (event: { payload: OAuthSuccessPayload }) => {
    showToast(`🎉 OAuth capturado para ${event.payload.provider}!`);
    refreshStatus();
  });
}

// Initial fetch and interval
refreshStatus();
setInterval(refreshStatus, 3000);
