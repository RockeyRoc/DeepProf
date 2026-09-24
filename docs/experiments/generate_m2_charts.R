#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
base_dir <- if (length(args) >= 1) normalizePath(args[[1]], mustWork = TRUE) else normalizePath("docs/experiments", mustWork = TRUE)
data_file <- file.path(base_dir, "source-data", "m2-validation.csv")
figure_dir <- file.path(base_dir, "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

suppressPackageStartupMessages(library(ggplot2))
suppressPackageStartupMessages(library(svglite))
suppressPackageStartupMessages(library(ragg))

data <- read.csv(data_file, fileEncoding = "UTF-8", stringsAsFactors = FALSE, check.names = FALSE)
data <- subset(data, figure_id == "F06")
if (nrow(data) != 3L || any(!is.finite(data$passed)) || any(!is.finite(data$failed)) ||
    any(!is.finite(data$total)) || any(data$total <= 0) || any(data$passed < 0) ||
    any(data$failed < 0) || any(data$passed + data$failed != data$total)) {
  stop("F06 data must contain three complete, non-negative test-result rows.")
}

data$verification <- factor(data$verification_id,
  levels = c("cli_unit", "m2_focused", "python_full"),
  labels = c("CLI \u5355\u5143\u6d4b\u8bd5", "M2 \u79bb\u7ebf\u5b9a\u5411\u9a8c\u6536", "Python \u5168\u91cf\u56de\u5f52"))
data$runtime_group <- factor(data$runtime_group, levels = c("Python", "Node.js"))
data$label <- sprintf("%d/%d passed | %d failed", data$passed, data$total, data$failed)

max_count <- max(data$total)
plot <- ggplot(data, aes(x = passed, y = verification, fill = runtime_group)) +
  geom_col(width = 0.56, colour = "white", linewidth = 0.35) +
  geom_text(aes(label = label), hjust = -0.08, size = 3.1, colour = "#253746") +
  scale_fill_manual(values = c(Python = "#315D78", `Node.js` = "#477A66"), guide = "none") +
  scale_x_continuous(limits = c(0, max_count * 1.42), breaks = scales::pretty_breaks(6),
                     expand = expansion(mult = c(0, 0.01))) +
  labs(
    title = "M2 \u81ea\u52a8\u5316\u9a8c\u6536\u7ed3\u679c",
    subtitle = "\u67f1\u957f\u4e3a\u901a\u8fc7\u7684\u56fa\u5b9a\u6d4b\u8bd5\u9879\u6570\uff1bM2 \u5b9a\u5411\u96c6\u5305\u542b\u5728 Python \u5168\u91cf\u56de\u5f52\u4e2d\uff0c\u4e24\u8005\u4e0d\u53ef\u76f8\u52a0\uff0c\u4e5f\u4e0d\u4ee3\u8868\u5b66\u751f\u5b66\u4e60\u6548\u679c\u3002",
    x = "\u901a\u8fc7\u7684\u6d4b\u8bd5\u9879\u6570",
    y = NULL
  ) +
  theme_minimal(base_size = 10, base_family = "sans") +
  theme(
    plot.title = element_text(face = "bold", size = 13, colour = "#243746"),
    plot.subtitle = element_text(size = 9, colour = "#5D6870", margin = margin(b = 12)),
    axis.title.x = element_text(size = 9, colour = "#34424A"),
    axis.text = element_text(size = 9, colour = "#34424A"),
    panel.grid.minor = element_blank(),
    panel.grid.major = element_blank(),
    plot.margin = margin(12, 42, 10, 10)
  )

stem <- file.path(figure_dir, "F06-m2-validation")
ragg::agg_png(paste0(stem, ".png"), width = 8.2, height = 3.6, units = "in", res = 400, background = "white")
print(plot)
grDevices::dev.off()
ragg::agg_tiff(paste0(stem, ".tiff"), width = 8.2, height = 3.6, units = "in", res = 600,
               compression = "lzw", background = "white")
print(plot)
grDevices::dev.off()
svglite::svglite(paste0(stem, ".svg"), width = 8.2, height = 3.6, bg = "white")
print(plot)
grDevices::dev.off()
grDevices::cairo_pdf(paste0(stem, ".pdf"), width = 8.2, height = 3.6, family = "sans", bg = "white")
print(plot)
grDevices::dev.off()
cat("Rendered F06 M2 validation chart to ", figure_dir, "\n", sep = "")
