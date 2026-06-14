"""通用分析 runner —— 跑任意用户定义的 analysis.R。

本身不含任何具体分析逻辑:从 job.params 读 analysis_folder + inputs + analysis_params,
写一份 io.json,调通用执行器 backend-r/run_analysis.R 去 source 那个 analysis.R
并执行它的 run(inputs, params, out_dir)。

job.params:
  analysis_folder:  分析所在文件夹(注册表给出),里面有 analysis.R
  analysis_id:      分析 id(仅日志可读)
  inputs:           { 数据类型: 文件路径 },例如 {"deg_table": "/.../deseq2_all.tsv"}
  analysis_params:  用户在表单里填的参数 { key: value }
"""
import json
import os
from pathlib import Path

from runners.base import BaseRunner


class AnalysisRunner(BaseRunner):
    def run(self):
        params = self.job.params or {}
        folder = params.get("analysis_folder")
        inputs = params.get("inputs") or {}
        analysis_params = params.get("analysis_params") or {}

        if not folder:
            raise ValueError("缺 analysis_folder")
        analysis_r = Path(folder) / "analysis.R"
        if not analysis_r.exists():
            raise FileNotFoundError(f"分析脚本不存在: {analysis_r}")

        out_dir = self.output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)

        for k, p in inputs.items():
            if p and not Path(p).exists():
                self.log(f"!! 输入 {k} 路径不存在: {p}")

        self.update(pct=5, stage="准备", detail=Path(folder).name)
        io = {"inputs": inputs, "params": analysis_params, "out_dir": str(out_dir)}
        io_path = out_dir / "_io.json"
        io_path.write_text(json.dumps(io, ensure_ascii=False, indent=2), encoding="utf-8")

        from runners.dispatcher import module_root

        rscript = module_root() / "env/bin/Rscript"
        harness = module_root() / "backend-r" / "run_analysis.R"
        if not rscript.exists():
            raise FileNotFoundError(f"模块 env 缺 Rscript: {rscript}")
        if not harness.exists():
            raise FileNotFoundError(f"通用执行器不存在: {harness}")

        env = dict(os.environ)
        env["PATH"] = str(module_root() / "env/bin") + ":" + env.get("PATH", "")
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env.pop(k, None)

        cmd = [str(rscript), str(harness), str(analysis_r), str(io_path)]
        self.update(pct=15, stage="运行分析")
        self.log(f"$ {' '.join(cmd)}")
        self.run_command(cmd, env=env, cwd=str(module_root() / "backend-r"))

        self.update(pct=100, stage="完成")
        self.log("=== 分析完成 ===")


if __name__ == "__main__":
    AnalysisRunner.main()
