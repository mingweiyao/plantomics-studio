# PlantOmics ONT LncDRS Module

ONT lncRNA Direct RNA Sequencing (DRS) module for PlantOmics Studio.

## Overview

This module provides a comprehensive analysis pipeline for Oxford Nanopore
Technologies (ONT) Direct RNA Sequencing data, focused on lncRNA discovery
and characterization.

## Pipeline Steps

1. **Basecalling** - Dorado basecalling (pod5 -> FASTQ)
2. **QC Filtering** - NanoFilt (q>=10, len>=50)
3. **poly(A) Detection** - Dorado --estimate-poly-a for poly(A) proportion estimation
4. **rRNA Removal** - minimap2 against rRNA database
5. **Alignment** - minimap2 splice-aware alignment (-ax splice -uf)
6. **Transcript Assembly** - Pinfish consensus + StringTie merge
7. **Novel Transcript Classification** - gffcompare
8. **CDS Prediction** - TransDecoder
9. **Functional Annotation** - 7 database annotation (Nr, UniProt, Pfam, KEGG, EggNOG, GO, SignalP)
10. **lncRNA Identification** - CPC2 + PLEK consensus
11. **Quantification** - Salmon
12. **Alternative Splicing** - SUPPA2
13. **Fusion Detection** - Chimeric read analysis
14. **poly(A) Analysis** - Poly(A) tail length statistics and differential analysis

## Key Differences from Standard lncRNA Module

- Uses **Dorado** (not Guppy) for basecalling
- QC threshold: **q>=10** (not q>=7)
- Includes **poly(A) proportion detection** and **poly(A) length analysis**
- **No Pychopper** (not needed for DRS)
- DRS-specific minimap2 parameters
- RNA modification detection support

## Version

1.0.0
