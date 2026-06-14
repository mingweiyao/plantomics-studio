/**
 * 转录组模块的前端 API 客户端
 * 
 * 通过主程序的 /modules/omics-rnaseq-bulk/<path> 代理调用模块后端。
 * 这一层的存在让模块相关代码集中、类型清晰。
 */
import { coreApi } from "./api";

const MODULE_ID = "omics-rnaseq-bulk";

// ─────────────────────────────────────────────
// 任务模型
// ─────────────────────────────────────────────

export type JobStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type JobKind =
  | "sra_download"
  | "sra_extract"
  | "fastqc"
  | "fastp"
  | "star_index"
  | "star_align"
  | "feature_counts"
  | "merge_counts"
  | "normalize"
  | "library_qc"
  | "new_transcripts"
  | "alt_splicing"
  | "lncrna"
  | "pipeline_upstream";

export interface JobProgress {
  pct: number;
  stage: string;
  detail: string;
  // 无法估算进度的长步骤(STAR 单样本比对、DESeq()、WGCNA)→ 流动动画
  indeterminate?: boolean;
  // 心跳时间戳;即使 pct 不变也会刷新,证明任务还活着
  heartbeat?: string;
}

export interface Job {
  id: string;
  kind: JobKind;
  project_id: string;
  params: Record<string, any>;
  output_path: string;
  output_subdir: string;
  status: JobStatus;
  progress: JobProgress;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  pid: number | null;
}

export const JOB_KIND_LABELS: Record<JobKind, string> = {
  sra_download: "SRA 下载",
  sra_extract: "SRA 解压",
  fastqc: "FastQC 质控",
  fastp: "fastp 过滤",
  star_index: "STAR 索引",
  star_align: "STAR 比对",
  feature_counts: "featureCounts 量化",
  merge_counts: "合并 counts",
  normalize: "TPM/FPKM 标准化",
  library_qc: "文库质控 (Qualimap)",
  new_transcripts: "新转录本 (StringTie)",
  alt_splicing: "可变剪接 (rMATS)",
  lncrna: "lncRNA 预测",
  pipeline_upstream: "一键上游分析",
};

export const TERMINAL_STATUSES: JobStatus[] = [
  "completed",
  "failed",
  "cancelled",
  "interrupted",
];

export function isTerminal(s: JobStatus): boolean {
  return TERMINAL_STATUSES.includes(s);
}

// ─────────────────────────────────────────────
// 通用 wrapper
// ─────────────────────────────────────────────

async function call<T = any>(
  path: string,
  method: "GET" | "POST" | "PUT" | "DELETE" = "POST",
  body?: any
): Promise<T> {
  return coreApi.callModule<T>({
    module_id: MODULE_ID,
    path,
    method,
    body,
  });
}

// ─────────────────────────────────────────────
// 模块信息
// ─────────────────────────────────────────────

export const rnaseqApi = {
  // 模块状态
  health: () => call<{ status: string }>("/health", "GET"),
  info: () =>
    call<{
      module_id: string;
      version: string;
      supported_jobs: JobKind[];
    }>("/info", "GET"),

  // ─── 任务 ───
  listJobs: (project_id?: string) =>
    call<{ jobs: Job[] }>(
      `/jobs${project_id ? `?project_id=${project_id}` : ""}`,
      "GET"
    ),

  getJob: (job_id: string) => call<Job>(`/jobs/${job_id}`, "GET"),

  getJobLog: (job_id: string, tail?: number) =>
    call<string>(
      `/jobs/${job_id}/log${tail ? `?tail=${tail}` : ""}`,
      "GET"
    ),

  cancelJob: (job_id: string) =>
    call<{ cancelled: string }>(`/jobs/${job_id}/cancel`, "POST"),

  deleteJob: (job_id: string) =>
    call<{ deleted: string }>(`/jobs/${job_id}`, "DELETE"),

  getConcurrency: () =>
    call<{
      max_concurrent: number;
      total_threads: number;
      max_parallel: number;
      threads_per_job: number;
    }>("/concurrency", "GET"),
  setConcurrency: (max_concurrent: number, total_threads?: number) =>
    call<{
      max_concurrent: number;
      total_threads: number;
      max_parallel: number;
      threads_per_job: number;
    }>("/concurrency", "PUT", { max_concurrent, total_threads }),

  // ─── 提交任务 ───
  // 每种返回新建的 Job。提交后用 listJobs / getJob 轮询。
  submitSra: (req: {
    project_id: string;
    output_path: string;
    params: { accessions: string[]; threads?: number };
  }) => call<Job>("/submit/sra-download", "POST", req),

  submitFastqc: (req: {
    project_id: string;
    output_path: string;
    params: { fastq_files: string[]; threads?: number };
  }) => call<Job>("/submit/fastqc", "POST", req),

  submitFastp: (req: {
    project_id: string;
    output_path: string;
    params: {
      samples: { name: string; r1: string; r2?: string }[];
      qualified_quality_phred?: number;
      unqualified_percent_limit?: number;
      length_required?: number;
      adapter_sequence_r1?: string;
      adapter_sequence_r2?: string;
      threads?: number;
    };
  }) => call<Job>("/submit/fastp", "POST", req),

  submitStarIndex: (req: {
    project_id: string;
    output_path: string;
    params: {
      fasta: string;
      gtf?: string;
      threads?: number;
      sjdb_overhang?: number | "auto";        // 单值(老兼容)
      sjdb_overhangs?: number[] | "auto";     // 多值(新)— 一次建多个索引
      sample_fastq_dir?: string;
      genomeSAindexNbases?: number;
    };
  }) => call<Job>("/submit/star-index", "POST", req),

  submitStarAlign: (req: {
    project_id: string;
    output_path: string;
    params: {
      index_dir?: string;     // 老格式:直接给某个索引目录
      index_root?: string;    // 新格式:star_index/ 根目录,自动按读长选子索引
      samples: { name: string; r1: string; r2?: string }[];
      threads?: number;
    };
  }) => call<Job>("/submit/star-align", "POST", req),

  submitFeatureCounts: (req: {
    project_id: string;
    output_path: string;
    params: {
      bam_files: string[];
      gtf: string;
      paired?: boolean;
      strand?: number;
      threads?: number;
    };
  }) => call<Job>("/submit/feature-counts", "POST", req),

  submitLibraryQc: (req: {
    project_id: string;
    output_path: string;
    params: {
      bam_files: string[];
      gtf: string;
      sample_names?: string[];
      paired?: boolean;
      java_mem?: string;
    };
  }) => call<Job>("/submit/library-qc", "POST", req),

  submitNewTranscripts: (req: {
    project_id: string;
    output_path: string;
    params: {
      bam_files: string[];
      gtf: string;
      sample_names?: string[];
      strand?: number;
      threads?: number;
    };
  }) => call<Job>("/submit/new-transcripts", "POST", req),

  submitAltSplicing: (req: {
    project_id: string;
    output_path: string;
    params: {
      bam_files_g1: string[];
      bam_files_g2: string[];
      gtf: string;
      read_length?: number;
      paired?: boolean;
      threads?: number;
    };
  }) => call<Job>("/submit/alt-splicing", "POST", req),

  submitLncrna: (req: {
    project_id: string;
    output_path: string;
    params: {
      candidate_gtf: string;
      gtf: string;
      genome_fasta: string;
      threads?: number;
    };
  }) => call<Job>("/submit/lncrna", "POST", req),

  submitMergeCounts: (req: {
    project_id: string;
    output_path: string;
    params: {
      counts_dir?: string;
      counts_files?: string[];
      output_name?: string;
    };
  }) => call<Job>("/submit/merge-counts", "POST", req),

  submitNormalize: (req: {
    project_id: string;
    output_path: string;
    params: {
      mode?: "matrix" | "per_sample";
      counts_file?: string;
      counts_files?: string[];
      gtf?: string;
      methods?: ("TPM" | "FPKM" | "CPM")[];
    };
  }) => call<Job>("/submit/normalize", "POST", req),

  // ─── 一键运行 ───
  submitPipelineUpstream: (req: {
    project_id: string;
    output_path: string;
    params: {
      workdir: string;
      fasta?: string;
      gtf: string;
      steps?: string[];
      fastp?: any;
      star_align?: any;
      feature_counts?: any;
    };
  }) => call<Job>("/submit/pipeline-upstream", "POST", req),
};
