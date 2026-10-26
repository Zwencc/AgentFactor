export interface NativeOptions {
  principalId: string
  projectId: string
  workspace: string
  agentName: string
  agentVersion?: string
  apiBase?: string
}

export interface NativeResponse {
  assurance?: string
  result?: Record<string, unknown>
  [key: string]: unknown
}

export class NativeSDKError extends Error {}

export class AgentFactorNative {
  private readonly options: NativeOptions
  private readonly apiBase: string
  private sessionId?: string
  private token?: string
  public assurance?: string

  constructor(options: NativeOptions) {
    this.options = options
    this.apiBase = (options.apiBase ?? "http://127.0.0.1:9889").replace(/\/$/, "")
  }

  async start(): Promise<NativeResponse> {
    if (this.sessionId)
      throw new NativeSDKError("Native session is already active")
    const value = await this.post("/security/v1alpha1/native/sessions", {
      principal: { id: this.options.principalId, authn: "native-sdk" },
      project_id: this.options.projectId,
      workspace_path: this.options.workspace,
      agent: { name: this.options.agentName, version: this.options.agentVersion },
    })
    this.sessionId = this.requiredString(value, "id")
    this.token = this.requiredString(value, "token")
    this.assurance = typeof value.assurance === "string" ? value.assurance : undefined
    const { token: _token, ...safe } = value
    return safe
  }

  async close(): Promise<void> {
    if (!this.sessionId || !this.token)
      return
    await this.post(`/security/v1alpha1/native/sessions/${this.sessionId}/close`, { token: this.token })
    this.sessionId = undefined
    this.token = undefined
  }

  async readText(path: string): Promise<string> {
    const value = await this.filesystem("read_text", path)
    const content = value.result?.content
    if (typeof content !== "string")
      throw new NativeSDKError("AgentFactor API returned an invalid filesystem response")
    return content
  }

  async writeText(path: string, content: string): Promise<NativeResponse> {
    return this.filesystem("write_text", path, content)
  }

  async mkdir(path: string): Promise<NativeResponse> {
    return this.filesystem("mkdir", path)
  }

  async deleteFile(path: string): Promise<NativeResponse> {
    return this.filesystem("delete_file", path)
  }

  async listDirectory(path = "."): Promise<string[]> {
    const value = await this.filesystem("list", path)
    const entries = value.result?.entries
    if (!Array.isArray(entries) || entries.some(item => typeof item !== "string"))
      throw new NativeSDKError("AgentFactor API returned an invalid directory response")
    return entries as string[]
  }

  async run(argv: string[], cwd = ".", timeoutSeconds = 120): Promise<NativeResponse> {
    return this.authorizedPost("/security/v1alpha1/native/process", {
      argv,
      cwd,
      timeout_seconds: timeoutSeconds,
    })
  }

  async callMCP(serverId: string, toolName: string, argumentsValue: Record<string, unknown> = {}): Promise<NativeResponse> {
    return this.authorizedPost("/security/v1alpha1/native/mcp", {
      server_id: serverId,
      tool_name: toolName,
      arguments: argumentsValue,
    })
  }

  async networkRequest(
    method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
    url: string,
    options: { headers?: Record<string, string>, body?: string, timeoutSeconds?: number } = {},
  ): Promise<NativeResponse> {
    return this.authorizedPost("/security/v1alpha1/native/network", {
      method,
      url,
      headers: options.headers ?? {},
      body: options.body,
      timeout_seconds: options.timeoutSeconds ?? 30,
    })
  }

  async complete(
    providerId: string,
    model: string,
    messages: Array<Record<string, unknown>>,
    parameters: Record<string, unknown> = {},
    timeoutSeconds = 120,
  ): Promise<NativeResponse> {
    return this.authorizedPost("/security/v1alpha1/native/model", {
      provider_id: providerId,
      model,
      messages,
      parameters,
      timeout_seconds: timeoutSeconds,
    })
  }

  async declassify(ruleId: string, purpose: string): Promise<NativeResponse> {
    return this.authorizedPost("/security/v1alpha1/native/declassify", {
      rule_id: ruleId,
      purpose,
    })
  }

  private filesystem(operation: string, path: string, content?: string): Promise<NativeResponse> {
    return this.authorizedPost("/security/v1alpha1/native/filesystem", { operation, path, content })
  }

  private authorizedPost(path: string, payload: Record<string, unknown>): Promise<NativeResponse> {
    if (!this.sessionId || !this.token)
      throw new NativeSDKError("Native session has not been started")
    return this.post(path, { ...payload, session_id: this.sessionId, token: this.token })
  }

  private async post(path: string, payload: Record<string, unknown>): Promise<NativeResponse> {
    let response: Response
    try {
      response = await fetch(`${this.apiBase}${path}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      })
    }
    catch (error) {
      throw new NativeSDKError(`AgentFactor API is unavailable: ${String(error)}`)
    }
    const value: unknown = await response.json().catch(() => ({}))
    if (!response.ok) {
      const detail = this.isRecord(value) && typeof value.detail === "string" ? value.detail : "native_request_failed"
      throw new NativeSDKError(detail)
    }
    if (!this.isRecord(value))
      throw new NativeSDKError("AgentFactor API returned an invalid response")
    return value
  }

  private requiredString(value: NativeResponse, key: string): string {
    const item = value[key]
    if (typeof item !== "string")
      throw new NativeSDKError(`AgentFactor API response is missing ${key}`)
    return item
  }

  private isRecord(value: unknown): value is NativeResponse {
    return typeof value === "object" && value !== null && !Array.isArray(value)
  }
}
