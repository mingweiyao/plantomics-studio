/**
 * PlantOmics Studio 前端 API 客户端
 *
 * 主程序后端在 Tauri 启动时由 sidecar.rs 启动,监听一个动态端口。
 * 前端通过 Tauri command `core_call` 调用,Rust 端代为转发到 127.0.0.1:<port>。
 */
import { invoke } from "@tauri-apps/api/core";

async function call<T = any>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  return invoke<T>("core_call", {
    args: { method, path, body: body ?? null },
  });
}

export async function getBackendPort(): Promise<number | null> {
  return invoke<number | null>("get_backend_port");
}

// ─────────────────────────────────────────────────────────────────────
// 类型
// ─────────────────────────────────────────────────────────────────────

export interface ProjectCompute {
  total_threads: number;  // 该项目可用的总线程预算
}

export interface Project {
  id: string;
  name: string;
  description: string;
  workdir: string;
  reference_fasta: string | null;
  reference_gtf: string | null;
  modules_used: string[];
  module_data: Record<string, any>;
  upstream_params: Record<string, any>;
  compute?: ProjectCompute;
  created_at: string;
  updated_at: string;
}

export interface ModuleManifest {
  id: string;
  name: string;
  version: string;
  description?: string;
  icon?: string;
  category?: string;
  extends?: {
    project_types?: any[];
    menu_items?: any[];
  };
  runtime?: any;
}

export interface InstalledModule {
  id: string;
  version: string;
  manifest: ModuleManifest;
  status: "loading" | "ready" | "error" | "disabled";
  error?: string | null;
  py_port?: number;
  r_port?: number;
}

// ─────────────────────────────────────────────────────────────────────
// API
// ─────────────────────────────────────────────────────────────────────

export const coreApi = {
  health: () => call<{ status: string }>("GET", "/health"),
  info: () =>
    call<{
      app: string;
      version: string;
      modules_loaded: number;
    }>("GET", "/"),

  // ─── 项目 ───
  listProjects: () => call<{ projects: Project[] }>("GET", "/projects/"),

  createProject: (args: {
    name: string;
    description?: string;
    workdir: string;
    reference_fasta?: string;
    reference_gtf_or_gff?: string;
    total_threads?: number;
  }) => call<Project>("POST", "/projects/", args),

  getProject: (id: string) => call<Project>("GET", `/projects/${id}`),

  updateProject: (
    id: string,
    args: {
      name?: string;
      description?: string;
      reference_fasta?: string;
      reference_gtf?: string;
      total_threads?: number;
    }
  ) => call<Project>("PATCH", `/projects/${id}`, args),

  deleteProject: (id: string) =>
    call<{
      deleted: string;
      workdir_preserved: string;
      message: string;
    }>("DELETE", `/projects/${id}`),

  setProjectModuleData: (id: string, module_id: string, data: any) =>
    call<Project>("PUT", `/projects/${id}/module-data/${module_id}`, { data }),

  removeProjectModuleData: (id: string, module_id: string) =>
    call<Project>("DELETE", `/projects/${id}/module-data/${module_id}`),

  setUpstreamParams: (id: string, step: string, params: any) =>
    call<Project>("PUT", `/projects/${id}/upstream-params`, { step, params }),

  // 扫描工作目录:发现样本结构 / SRA 文件
  scanSamples: (id: string, stage: "raw" | "trimmed" | "aligned") =>
    call<{
      samples: { name: string; r1?: string; r2?: string | null; bam?: string }[];
      bams: string[];
    }>("GET", `/projects/${id}/scan-samples?stage=${stage}`),

  scanSra: (id: string) =>
    call<{
      sra_files: string[];
      fastq_files: string[];
      scan_dir: string;
    }>("GET", `/projects/${id}/scan-sra`),

  scanReadlengths: (id: string, subdir: "raw" | "trimmed") =>
    call<{
      records: {
        sample: string;
        files: string[];
        raw_read_length: number;  // 探测到的实际值(R1/R2 取大)
        read_length: number;      // 归到标准档后的值
        sjdb_overhang: number;
      }[];
      unique_overhangs: number[];
      scan_dir: string;
    }>("GET", `/projects/${id}/scan-readlengths?subdir=${subdir}`),

  // ─── 模块 ───
  listInstalledModules: () =>
    call<{ modules: InstalledModule[] }>("GET", "/modules-mgmt/installed"),
  getCatalog: () =>
    call<{ catalog: any[] }>("GET", "/modules-mgmt/catalog"),
  installLocalDeb: (deb_path: string, password?: string) =>
    call<{ success: boolean; message: string; log: string }>(
      "POST",
      "/modules-mgmt/install-local",
      { deb_path, password }
    ),
  uninstallModule: (module_id: string, password?: string) =>
    call<{ success: boolean; message: string; log: string }>(
      "POST",
      `/modules-mgmt/uninstall/${module_id}`,
      { password }
    ),

  // ─── 调用模块 ───
  callModule: <T = any>(args: {
    module_id: string;
    path: string;
    method?: string;
    body?: unknown;
  }) =>
    call<T>(
      args.method || "POST",
      `/modules/${args.module_id}/${args.path.replace(/^\//, "")}`,
      args.body
    ),
};
