import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { coreApi } from "../lib/api";
import { getGlobalConcurrency, setGlobalConcurrency } from "../lib/rnaseqApi";
import { PageHeader, Card, Loading, Field, Input } from "../components/ui";

export function Settings() {
  const { data: info, isLoading } = useQuery({
    queryKey: ["info"],
    queryFn: coreApi.info,
  });

  const [backendPort, setBackendPort] = useState<number | null>(null);
  useEffect(() => {
    invoke<number>("get_backend_port")
      .then(setBackendPort)
      .catch(() => setBackendPort(null));
  }, []);

  const [concurrency, setConcurrency] = useState<number>(getGlobalConcurrency());

  return (
    <div className="p-6 max-w-3xl">
      <PageHeader title="设置" />

      {isLoading ? (
        <Loading />
      ) : (
        <div className="space-y-4">
          <Card>
            <div className="text-sm font-medium mb-3">系统信息</div>
            <div className="text-xs space-y-2">
              <Row k="应用名称" v={info?.app || "PlantOmics Studio"} />
              <Row k="主程序版本" v={`v${info?.version || "1.0.0"}`} />
              <Row k="已加载模块" v={`${info?.modules_loaded ?? 0} 个`} />
              {backendPort && (
                <Row k="后端端口" v={`127.0.0.1:${backendPort}`} mono />
              )}
            </div>
          </Card>

          <Card>
            <div className="text-sm font-medium mb-3">数据位置</div>
            <div className="text-xs space-y-2">
              <Row k="项目数据" v="~/.plantomics/projects/" mono />
              <Row k="参考资源" v="~/.plantomics/references/" mono />
              <Row k="主程序" v="/opt/plantomics-studio/" mono />
              <Row k="模块" v="/opt/plantomics-studio/modules/" mono />
            </div>
          </Card>

          <Card>
            <div className="text-sm font-medium mb-3">计算资源(全局)</div>
            <Field
              label="同时运行任务数"
              hint="最多同时跑几个任务。每个任务实际用到的线程 = 该项目的总线程预算 ÷ 这个并行度。默认 1(每个任务用满项目预算);想并行才调大。"
            >
              <Input
                type="number"
                min={1}
                value={concurrency}
                onChange={(e) => {
                  const n = Math.max(1, parseInt(e.target.value) || 1);
                  setConcurrency(n);
                  setGlobalConcurrency(n);
                }}
                className="w-32"
              />
            </Field>
            <div className="text-[11px] text-ink-faint mt-1.5">
              全局设置,对所有项目生效;下次进入分析页时同步给后端调度器。
            </div>
          </Card>

          <Card>
            <div className="text-sm font-medium mb-3">关于</div>
            <div className="text-xs text-ink-muted leading-relaxed">
              PlantOmics Studio 是一个模块化的植物组学分析平台。主程序提供项目和资源管理,
              具体的分析能力通过安装模块获得。每个项目在创建时设「总线程预算」,
              「同时运行任务数」在此处全局设置。
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-ink-faint">{k}</span>
      <span className={mono ? "font-mono" : ""}>{v}</span>
    </div>
  );
}
