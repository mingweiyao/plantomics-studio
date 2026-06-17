/**
 * 下游分析模块(omics-analysis)前端 API。
 * 通过主程序的 /modules/omics-analysis/<path> 代理(core_call,JSON)调用模块后端。
 */
import { coreApi } from "./api";

const MODULE_ID = "omics-analysis";

function m<T = any>(path: string, method = "POST", body?: unknown): Promise<T> {
  return coreApi.callModule<T>({ module_id: MODULE_ID, path, method, body });
}

export type ParamType = "number" | "int" | "bool" | "select" | "text" | "column";

export interface AnalysisParam {
  key: string;
  label: string;
  type: ParamType;
  default?: any;
  help?: string | null;
  options?: string[];
}

export interface AnalysisManifest {
  id: string;
  label: string;
  category: string;
  description?: string | null;
  accepts: string[];
  params: AnalysisParam[];
  outputs: string[];
  has_preview: boolean;
  examples: string[];
  source: string;
  folder: string;
}

export interface CreateAnalysisInput {
  id: string;
  label: string;
  category: string;
  accepts: string[];
  params: any[];
  code: string;
  preview_b64?: string | null;
  examples?: { name: string; content_b64: string }[];
}

export const analysisApi = {
  list: () =>
    m<{ analyses: AnalysisManifest[]; dataset_types: Record<string, string> }>(
      "/analyses",
      "GET"
    ),
  rescan: () => m<{ analyses: AnalysisManifest[] }>("/analyses/rescan", "POST"),
  preview: (id: string) =>
    m<{ preview: string | null }>(`/analyses/${id}/preview-b64`, "GET"),
  run: (req: {
    analysis_id: string;
    project_id: string;
    output_path: string;
    inputs: Record<string, string>;
    params: Record<string, any>;
  }) => m<{ id: string }>("/run", "POST", req),
  job: (id: string) =>
    m<{ id: string; status: string; progress?: { pct?: number; stage?: string }; output_path?: string; error?: string | null }>(
      `/jobs/${id}`,
      "GET"
    ),
  create: (req: CreateAnalysisInput) =>
    m<AnalysisManifest>("/analyses-json", "POST", req),
  remove: (id: string) => m<{ ok: boolean }>(`/analyses/${id}`, "DELETE"),
};
