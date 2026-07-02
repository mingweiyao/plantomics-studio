#* @get /health
#* @serializer unboxedJSON
function() {
  list(status = "ok", module_id = "omics-ont-lncdrs", version = "1.0.0", backend = "r")
}

#* @get /info
#* @serializer unboxedJSON
function() {
  list(module_id = "omics-ont-lncdrs", version = "1.0.0", backend = "r",
       r_port = as.integer(Sys.getenv("MODULE_R_PORT", "0")),
       py_port = as.integer(Sys.getenv("MODULE_PY_PORT", "0")),
       data_dir = Sys.getenv("PLANTOMICS_DATA_DIR", ""),
       module_data_dir = Sys.getenv("MODULE_DATA_DIR", ""))
}
