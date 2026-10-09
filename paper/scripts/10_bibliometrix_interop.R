# Adım 10: WoS dışa aktarımının bibliometrix ile okunması (birlikte çalışabilirlik doğrulaması).
#
# Kullanım:
#   Rscript paper/scripts/10_bibliometrix_interop.R <wos_export.txt> <çıktı klasörü> [bibliometrix kaynak klasörü]
#
# bibliometrix kuruluysa paket kullanılır. Kurulu değilse üçüncü argüman olarak bibliometrix'in
# GitHub kaynak klasörü (git clone https://github.com/massimoaria/bibliometrix) verilebilir;
# R/ altındaki işlevler doğrudan yüklenir (gerekli paketler: dplyr, stringr, stringi, tidyr,
# igraph, Matrix, purrr, readr).
#
# Çıktılar: bx_summary.csv (anahtar, değer), bx_histdata.csv (belge düzeyinde LCS), bx_index.csv
# (SR etiketi ile TR Dizin kimliği eşlemesi). 09_validation_audit.py bunları okuyup araçla karşılaştırır.

args <- commandArgs(TRUE)
wos_file <- normalizePath(args[1], mustWork = TRUE); out <- args[2]; src <- if (length(args) >= 3) args[3] else NA
suppressPackageStartupMessages({
  library(dplyr); library(stringr); library(stringi); library(tidyr)
  library(igraph); library(Matrix); library(purrr); library(readr)
})

bx_version <- NA
if (requireNamespace("bibliometrix", quietly = TRUE)) {
  suppressPackageStartupMessages(library(bibliometrix))
  bx_version <- as.character(packageVersion("bibliometrix"))
} else {
  if (is.na(src)) stop("bibliometrix kurulu değil; kaynak klasörünü üçüncü argüman olarak verin")
  rdir <- file.path(src, "R")
  load(file.path(rdir, "sysdata.rda"), envir = globalenv())
  for (f in list.files(file.path(src, "data"), full.names = TRUE)) load(f, envir = globalenv())
  # Paket kurulu olmadığı için data(..., package = "bibliometrix") çağrılarını global ortamdan karşıla.
  data <- function(..., package = NULL, envir = parent.frame()) {
    nm <- c(...)
    for (n in nm) if (exists(n, envir = globalenv())) assign(n, get(n, envir = globalenv()), envir = envir)
    invisible(nm)
  }
  for (f in setdiff(list.files(rdir, pattern = "[.][Rr]$"), "biblioshiny.R"))
    tryCatch(source(file.path(rdir, f)), error = function(e) NULL)
  bx_version <- paste0(read.dcf(file.path(src, "DESCRIPTION"), fields = "Version")[1], " (source)")
}

dir.create(out, showWarnings = FALSE, recursive = TRUE)
M <- convert2df(wos_file, dbsource = "wos", format = "plaintext")
res <- biblioAnalysis(M, sep = ";")
yr <- table(M$PY); span <- max(M$PY, na.rm = TRUE) - min(M$PY, na.rm = TRUE)
M_co <- metaTagExtraction(M, Field = "AU_CO", sep = ";")
h <- suppressWarnings(histNetwork(M, sep = ";", verbose = FALSE))

kv <- c(
  bibliometrix_version = bx_version,
  documents = nrow(M),
  timespan = paste(range(M$PY, na.rm = TRUE), collapse = "-"),
  sources = length(unique(M$SO)),
  authors = length(res$Authors),
  single_authored_documents = sum(res$nAUperPaper == 1),
  authors_per_document = round(mean(res$nAUperPaper), 2),
  total_citations = sum(as.numeric(M$TC), na.rm = TRUE),
  mean_citations = round(mean(as.numeric(M$TC), na.rm = TRUE), 2),
  author_keywords = length(unique(trimws(unlist(strsplit(M$DE[!is.na(M$DE)], ";"))))),
  cited_references = sum(lengths(strsplit(M$CR[!is.na(M$CR) & M$CR != ""], ";"))),
  annual_growth_rate = round(((as.numeric(yr[length(yr)]) / as.numeric(yr[1]))^(1 / span) - 1) * 100, 2),
  documents_with_country = sum(!is.na(M_co$AU_CO) & M_co$AU_CO != ""),
  local_citations_total = sum(h$histData$LCS),
  documents_locally_cited = sum(h$histData$LCS > 0),
  duplicated_SR_labels = sum(duplicated(M$SR))
)
write.csv(data.frame(key = names(kv), value = unname(kv)), file.path(out, "bx_summary.csv"), row.names = FALSE)
write.csv(data.frame(Paper = h$histData$Paper, LCS = h$histData$LCS, GCS = h$histData$GCS),
          file.path(out, "bx_histdata.csv"), row.names = FALSE)
write.csv(data.frame(SR = M$SR, UT = M$UT, DI = M$DI, TC = M$TC),
          file.path(out, "bx_index.csv"), row.names = FALSE)
print(kv)
