#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
base_dir <- if (length(args) >= 1) normalizePath(args[[1]], mustWork = TRUE) else normalizePath("docs/experiments", mustWork = TRUE)
data_file <- file.path(base_dir, "source-data", "m3-live-pilot-live-cells.csv")
summary_file <- file.path(base_dir, "source-data", "m3-live-pilot-live.json")
figure_dir <- file.path(base_dir, "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(svglite))
suppressPackageStartupMessages(library(ragg))

cells <- read.csv(data_file, fileEncoding = "UTF-8", stringsAsFactors = FALSE, check.names = FALSE)
summary <- jsonlite::fromJSON(summary_file, simplifyVector = TRUE)
cells$group <- factor(cells$group, levels = c("A", "B", "C"))
if (nrow(cells) != 36L || any(table(cells$group) != 12L) ||
    any(cells$provider_requests > 1L) || sum(cells$provider_requests) != 29L ||
    sum(cells$provider_outcome == "finish_reason_length") != 26L ||
    sum(cells$provider_outcome == "completed" & cells$finish_reason == "stop") != 3L ||
    sum(cells$provider_outcome == "not_called") != 7L ||
    summary$limits$max_output_tokens_requested != 512L || summary$limits$max_retries != 0L) {
  stop("F08 data violates the frozen 36-cell / 29-request pilot audit.")
}

outcomes <- c("finish_reason_length", "completed", "not_called")
outcome_labels <- c(
  finish_reason_length = "finish_reason = length",
  completed = "finish_reason = stop",
  not_called = "No Provider request"
)
outcome_colours <- c(
  finish_reason_length = "#C87537",
  completed = "#2D806D",
  not_called = "#D9E0E4"
)
counts <- as.data.frame(table(cells$group, factor(cells$provider_outcome, levels = outcomes)))
names(counts) <- c("group", "outcome", "n")
counts$group <- factor(counts$group, levels = c("A", "B", "C"))
counts$outcome <- factor(counts$outcome, levels = outcomes,
                         labels = unname(outcome_labels[outcomes]))
counts$segment_label <- ifelse(counts$n > 0L, as.character(counts$n), "")

palette <- setNames(unname(outcome_colours[outcomes]), unname(outcome_labels[outcomes]))
figure <- ggplot(counts, aes(x = group, y = n, fill = outcome)) +
  geom_col(width = 0.62, colour = "white", linewidth = 0.45) +
  geom_text(aes(label = segment_label), position = position_stack(vjust = 0.5),
            size = 3.6, fontface = "bold", colour = "#243746") +
  scale_fill_manual(values = palette, breaks = unname(outcome_labels[outcomes])) +
  scale_y_continuous(limits = c(0, 12.9), breaks = seq(0, 12, 3),
                     expand = expansion(mult = c(0, 0))) +
  labs(
    title = "M3 live pilot: Provider outcomes by group",
    subtitle = "36 constructed cells | 29 Provider requests | 512-token cap | 0 retries",
    x = "Group",
    y = "Cells (12 per group)",
    fill = "Provider outcome",
    caption = paste(
      "A request ending with finish_reason=length is counted as truncated, even when the app terminal state was OK.",
      "There were 26 length endings, 3 stop endings and 7 cells with no generation request.",
      "Sixteen B/C cells ended at length while the app reported a completed turn; this is not generation success.",
      "One usage report showed 513 completion tokens against the configured 512-token limit. Constructed cases only;",
      "not teacher-rated and not evidence of student learning.", sep = "\n"
    )
  ) +
  theme_minimal(base_size = 10, base_family = "sans") +
  theme(
    plot.title = element_text(face = "bold", size = 15, colour = "#243746", margin = margin(b = 3)),
    plot.subtitle = element_text(size = 9.5, colour = "#5D6870", margin = margin(b = 12)),
    axis.title = element_text(size = 9, colour = "#34424A"),
    axis.text = element_text(size = 10, colour = "#34424A"),
    panel.grid.minor = element_blank(),
    panel.grid.major.x = element_blank(),
    legend.position = "bottom",
    legend.title = element_text(size = 8.5, colour = "#34424A"),
    legend.text = element_text(size = 8.5, colour = "#34424A"),
    plot.caption = element_text(size = 8, colour = "#5D6870", hjust = 0, lineheight = 1.15,
                                margin = margin(t = 9)),
    plot.margin = margin(12, 16, 10, 12)
  )

stem <- file.path(figure_dir, "F08-m3-live-pilot-results")
width_mm <- 183
height_mm <- 115
width_in <- width_mm / 25.4
height_in <- height_mm / 25.4
writeLines('{"schema_version":1,"verdict":"NOT APPLICABLE","reason":"Single ggplot panel; no between-panel alignment comparison."}',
           paste0(stem, ".alignment.json"), useBytes = TRUE)

ragg::agg_png(paste0(stem, ".png"), width = width_in, height = height_in, units = "in", res = 400, background = "white")
print(figure)
grDevices::dev.off()
ragg::agg_tiff(paste0(stem, ".tiff"), width = width_in, height = height_in, units = "in", res = 600,
               compression = "lzw", background = "white")
print(figure)
grDevices::dev.off()
svglite::svglite(paste0(stem, ".svg"), width = width_in, height = height_in, bg = "white")
print(figure)
grDevices::dev.off()
grDevices::cairo_pdf(paste0(stem, ".pdf"), width = width_in, height = height_in, family = "sans", bg = "white")
print(figure)
grDevices::dev.off()
cat("Rendered F08 M3 live pilot results chart.\n")
