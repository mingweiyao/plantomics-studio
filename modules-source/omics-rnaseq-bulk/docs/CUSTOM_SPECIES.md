# 非模式物种富集分析(任意物种 GO/KEGG)

如果你研究的不是拟南芥/水稻/人/鼠这种现成有 Bioconductor `org.<X>.db` 包的物种,
传统流程(`enrichGO` / `enrichKEGG`)就跑不动。这份文档教你绕过这个限制。

## 思路

`clusterProfiler` 有个万能函数 `enricher()`,只要你给它两张表就能富集:

- **TERM2GENE**:`term_id <TAB> gene_id` —— 一对一对应,长格式
- **TERM2NAME**:`term_id <TAB> term_name` —— 给结果表加个可读名字,可选

只要你能把基因映射到 GO term / KEGG pathway,就能富集 —— **不依赖任何 OrgDb 包**。

## 完整流程

### 第 1 步:跑 eggNOG-mapper

在线版最省事,不用本地装 eggNOG 数据库(几十 GB):

1. 拿到你物种的蛋白 FASTA(从基因组注释里 extract,工具:`gffread -y proteins.fa -g genome.fa annotation.gff`)
2. 上传到 [eggNOG-mapper 网页](http://eggnog-mapper.embl.de/)
3. 填邮箱(完成后会发链接),提交,等通知(通常 30 分钟到几小时)
4. 收到邮件后,下载 `MM_xxx.emapper.annotations` 文件

如果你蛋白多(>20 万),网页版会拒绝,需要本地装 eggNOG-mapper:
```bash
# 通过 conda
conda install -c bioconda eggnog-mapper
download_eggnog_data.py    # 下载数据库,会用 ~50 GB
emapper.py -i proteins.fa -o my_species --output_dir results/
```

### 第 2 步:在主程序里转换

打开 PlantOmics Studio,进入项目的"下游分析"页,选任一富集分析(GO/KEGG/GSEA)。
顶部会有一张提示卡片"非模式物种?需要自定义注释",点进去:

- **eggNOG-mapper annotations 文件**: 选第 1 步下载的 `*.emapper.annotations`
- **输出目录**: 默认是 `<workdir>/downstream/annotation/`(可改)
- 点"开始转换"

转换完成后输出目录里会有这些文件:
```
term2gene_go_BP.tsv         # GO biological process(常用)
term2gene_go_MF.tsv         # GO molecular function
term2gene_go_CC.tsv         # GO cellular component
term2gene_go_ALL.tsv        # 全部 GO,不分 ontology
term2gene_kegg.tsv          # KEGG pathway
term2name_go.tsv            # GO term ID → 名字(用 GO.db 自动查的)
term2name_kegg.tsv          # KEGG pathway ID → 名字(联网查 KEGG.jp)
conversion_summary.json
```

### 第 3 步:在富集表单里切到"自定义注释"模式

下游分析 → "GO 富集"(或 KEGG / GSEA),把"数据库类型"切到 **"自定义注释 (任意物种)"**:

- **TERM2GENE 文件**:GO 选 `term2gene_go_BP.tsv`(或 MF/CC/ALL),KEGG 选 `term2gene_kegg.tsv`
- **TERM2NAME 文件(可选)**:对应 `term2name_go.tsv` / `term2name_kegg.tsv`
  - GO 不传也行,R 会自动用 GO.db 反查
  - KEGG 不传则结果只显示 pathway ID(`ko00010` 这种),传了就有可读名字

提交,跑出来跟模式物种结果格式一样。

## 常见问题

### Q: TERM2NAME 不上传可以吗?
GO 可以(R 端用 GO.db 自动查名字)。KEGG 不传的话结果表里 `Description` 列就是空的或重复 pathway ID,
不影响富集计算本身,只是可读性差点。**强烈建议**让转换工具一并生成 KEGG term2name 文件。

### Q: 我的基因 ID 跟 eggNOG 输出对不上?
eggNOG-mapper 输出第一列(`#query`)是你提交时的 protein FASTA 序列名。如果你的 DEG
表里的 gene_id 跟 protein name 不一样(比如一个基因有多个转录本),需要先做一次映射:

- 通常 protein name 是 `geneID.transcript_id` 这种格式
- 你需要写脚本截取 gene 部分,或者把 DEG 表的 gene_id 替换成 protein name
- 或者在 `gffread -y` 提取 protein 时用 `-S` 选项指定 `-G` 把 ID 换成 gene 级

### Q: 没法联网,怎么生成 KEGG term2name?
转换时勾"跳过联网拿 KEGG pathway 名字"。或者你自己从其他来源弄一份
`pathway_id <TAB> pathway_name` 的 tsv 当 TERM2NAME 文件传。

### Q: 我想用 KOBAS / DIAMOND 流程的注释,不用 eggNOG
完全可以。**任何能产出"基因 → 注释 term"映射的工具都行**。
你只要把它的输出整理成 `term <TAB> gene` 两列 tsv,就能在自定义模式里用。
TERM2NAME 同理。
