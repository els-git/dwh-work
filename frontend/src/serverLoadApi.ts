// I13-13: server load for administrators (GET /api/admin/server/load). The API samples the server every 2 s
// and keeps 60 minutes; without `since` it returns only the current values (the badge), with `since` also
// the samples taken after that moment (0 = the whole hour).

export interface ServerLoadDisk {
  read: number
  write: number
  busy: number
}

export interface ServerLoadService {
  running: boolean
  cpu: number | null
  memory: number | null
}

export interface ServerLoadSample {
  at: number
  cpu: number
  iowait: number
  steal: number
  cores: number[]
  memory: { used: number, cache: number, swap_used: number }
  load: number[]
  disks: Record<string, ServerLoadDisk>
  net: { rx: number, tx: number }
  services: Record<string, ServerLoadService>
  postgres: Record<string, number> | null
}

export interface ServerFileSystem {
  path: string
  total: number
  free: number
  used_percent: number
}

export interface ServerLoad {
  interval_seconds: number
  history_seconds: number
  sampling: boolean
  cores: number
  memory_total: number | null
  swap_total: number | null
  current: ServerLoadSample | null
  file_systems: ServerFileSystem[]
  postgres_at: number | null
  samples: ServerLoadSample[] | null
}

export class ServerLoadHttpError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function getServerLoad(since?: number): Promise<ServerLoad> {
  const query = since === undefined ? '' : `?since=${encodeURIComponent(String(since))}`
  const response = await fetch(`/api/admin/server/load${query}`, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json() as { message?: string }
      message = payload.message ?? message
    } catch {
      // the status line is enough
    }
    throw new ServerLoadHttpError(response.status, message)
  }
  return response.json() as Promise<ServerLoad>
}
