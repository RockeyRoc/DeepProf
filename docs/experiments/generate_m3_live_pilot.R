#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
base_dir <- if (length(args) >= 1) normalizePath(args[[1]], mustWork = TRUE) else normalizePath("docs/experiments", mustWork = TRUE)
data_file <- file.path(base_dir, "source-data", "m3-live-pilot-preflight.csv")
figure_dir <- file.path(base_dir, "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(svglite))
suppressPackageStartupMessages(library(ragg))

data <- read.csv(data_file, fileEncoding = "UTF-8", stringsAsFactors = FALSE, check.names = FALSE)
data$group <- factor(data$group, levels = c("A", "B", "C"))
if (nrow(data) != 3L || anyDuplicated(data$group) ||
    any(data$planned_cases != 12L) || any(data$cases_with_retrieval_evidence + data$cases_insufficient_evidence != data$planned_cases) ||
    any(data$provider_cells_executed != 0L) || any(data$provider_calls != 0L)) {
  stop("F07 preflight data must contain three complete groups and no provider inference.")
}

palette <- c(evidence = "#315D78", gap = "#D88A35", pending = "#D8DEE3")
theme_f07 <- theme_minimal(base_size = 10, base_family = "sans") +
  theme(
    plot.title = element_text(face = "bold", size = 13, colour = "#243746"),
    plot.subtitle = element_text(size = 9, colour = "#5D6870", margin = margin(b = 9)),
    axis.title = element_text(size = 9, colour = "#34424A"),
    axis.text = element_text(size = 9, colour = "#34424A"),
    panel.grid.minor = element_blank(),
    panel.grid.major.y = element_blank(),
    legend.position = "none",
    plot.margin = margin(8, 10, 8, 8)
  )

row_levels <- as.vector(rbind(
  paste(data$group, "\u672C\u5730\u68C0\u7D22", sep = " \u00B7 "),
  paste(data$group, "Provider \u63A8\u7406", sep = " \u00B7 ")
))
retrieval_rows <- data.frame(
  group = data$group,
  row = paste(data$group, "\u672C\u5730\u68C0\u7D22", sep = " \u00B7 "),
  result = "retrievable",
  count = data$cases_with_retrieval_evidence,
  label = as.character(data$cases_with_retrieval_evidence),
  label_colour = "#FFFFFF"
)
gap_rows <- data.frame(
  group = data$group,
  row = paste(data$group, "\u672C\u5730\u68C0\u7D22", sep = " \u00B7 "),
  result = "gap",
  count = data$cases_insufficient_evidence,
  label = as.character(data$cases_insufficient_evidence),
  label_colour = "#263746"
)
pending_rows <- data.frame(
  group = data$group,
  row = paste(data$group, "Provider \u63A8\u7406", sep = " \u00B7 "),
  result = "pending",
  count = data$planned_cases,
  label = paste(data$planned_cases, "\u5F85\u6388\u6743"),
  label_colour = "#263746"
)
bar_data <- rbind(retrieval_rows, gap_rows, pending_rows)
bar_data$row <- factor(bar_data$row, levels = rev(row_levels))
bar_data$result <- factor(bar_data$result, levels = c("retrievable", "gap", "pending"))
executed_rows <- data.frame(
  group = data$group,
  row = factor(paste(data$group, "Provider \u63A8\u7406", sep = " \u00B7 "), levels = rev(row_levels)),
  count = data$provider_cells_executed,
  label = sprintf("%d / %d \u5DF2\u6267\u884C", data$provider_cells_executed, data$planned_cases)
)

figure <- ggplot(bar_data, aes(x = count, y = row, fill = result)) +
  geom_col(width = 0.62, colour = "white", linewidth = 0.4) +
  geom_text(aes(label = label, colour = label_colour), position = position_stack(vjust = 0.5),
            size = 3.0, fontface = "bold", show.legend = FALSE) +
  geom_point(data = executed_rows, aes(x = count, y = row), inherit.aes = FALSE,
             shape = 21, size = 2.4, stroke = 0.7, fill = palette[["evidence"]], colour = "white") +
  geom_text(data = executed_rows, aes(x = 0.35, y = row, label = label), inherit.aes = FALSE,
            hjust = 0, size = 3.0, colour = "#243746", fontface = "bold") +
  scale_fill_manual(values = c(retrievable = palette[["evidence"]], gap = palette[["gap"]],
                               pending = palette[["pending"]])) +
  scale_colour_identity() +
  scale_x_continuous(limits = c(-0.6, 14), breaks = seq(0, 12, 3), expand = expansion(mult = c(0, 0))) +
  labs(
    title = "M3 \u771F\u5B9E\u6A21\u578B\u8BD5\u70B9\uFF1A\u68C0\u7D22\u9884\u68C0\u5B8C\u6210\uFF0C\u63A8\u7406\u672A\u542F\u52A8",
    subtitle = "12 \u4E2A\u6784\u9020\u5F00\u53D1\u6848\u4F8B \u00D7 A/B/C\uFF1B\u6BCF\u884C\u8BA1\u5212\u5206\u6BCD\u4E3A 12\uFF0C\u771F\u5B9E\u63A8\u7406\u5408\u8BA1 0/36 \u683C",
    x = "\u6848\u4F8B\u683C\u6570",
    y = "",
    caption = "\u84DD\u8272\u8868\u793A\u6709\u53EF\u5B9A\u4F4D\u68C0\u7D22\u7ED3\u679C\uFF0C\u6A59\u8272\u8868\u793A\u8BC1\u636E\u4E0D\u8DB3\uFF0C\u7070\u8272\u8868\u793A\u7B49\u5F85\u6388\u6743\u3002\n\u5C1A\u672A\u8C03\u7528\u5916\u90E8\u6A21\u578B\uFF0C\u8D39\u7528\u672A\u4EA7\u751F\uFF1B\u6559\u5E08\u5BA1\u6838\u5F85\u5B8C\u6210\uFF0C\u5B66\u751F\u5B66\u4E60\u6548\u679C\u672A\u6D4B\u91CF\u3002"
  ) +
  theme_f07 + theme(
    plot.title = element_text(face = "bold", size = 15, colour = "#243746"),
    plot.subtitle = element_text(size = 10, colour = "#5D6870", margin = margin(b = 10)),
    plot.caption = element_text(size = 8.5, colour = "#5D6870", hjust = 0, margin = margin(t = 8)),
    axis.text.y = element_text(size = 9.5),
    plot.margin = margin(12, 18, 10, 10)
  )

stem <- file.path(figure_dir, "F07-m3-live-pilot-preflight")
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
cat("Rendered F07 M3 live pilot preflight chart.\n")
