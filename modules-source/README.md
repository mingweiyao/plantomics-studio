# 模块源码工作区

这个目录用于在主程序仓库内开发/管理模块的源码(可选)。

每个子目录是一个模块的源码,例如:

```
modules-source/
├── omics-rnaseq-bulk/      # 转录组模块源码
│   ├── module.yaml
│   ├── conda-deps.yaml
│   ├── backend-py/
│   └── ...
└── omics-proteomics/
```

模块**也可以**作为完全独立的 git repo 单独管理,这个目录只是方便。

主程序运行时**不**直接用这个目录。它只读 `/opt/plantomics-studio/modules/`。
但开发模式下,如果 `/opt/plantomics-studio/modules/` 不存在,主程序会回退到这个
目录扫描模块,方便开发。
