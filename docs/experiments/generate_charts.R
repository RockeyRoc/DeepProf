#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)
base_dir <- if (length(args) >= 1) normalizePath(args[[1]], mustWork = TRUE) else normalizePath("docs/experiments", mustWork = TRUE)
data_file <- file.path(base_dir, "source-data", "figure-data.csv")
figure_dir <- file.path(base_dir, "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

suppressPackageStartupMessages({
  library(ggplot2)
  library(patchwork)
  library(svglite)
  library(ragg)
})

skill_root <- Sys.getenv("NATURE_FIGURE_SKILL_ROOT", file.path(Sys.getenv("USERPROFILE"), ".codex", "skills", "nature-figure"))
alignment_helper <- file.path(skill_root, "scripts", "panel_alignment.R")
alignment_auditor <- file.path(skill_root, "scripts", "audit_panel_alignment.py")

# Use the platform's Arial-compatible sans-serif mapping so grid, ragg, SVG, and Cairo measure consistently.
font_family <- "sans"
theme_set(theme_minimal(base_size = 11, base_family = font_family) +
  theme(
    plot.title = element_text(face = "bold", size = 13, colour = "#243746"),
    plot.subtitle = element_text(size = 9, colour = "#5D6870"),
    axis.title = element_text(size = 10, colour = "#34424A"),
    axis.text = element_text(size = 9, colour = "#34424A"),
    panel.grid.minor = element_blank(),
    panel.grid.major.y = element_blank(),
    legend.position = "bottom",
    legend.text = element_text(size = 9),
    plot.margin = margin(12, 18, 10, 10)
  ))

palette <- c(
  deterministic = "#315D78", manual = "#D2A35B", reference_pending = "#9B5D5B",
  completed = "#477A66", failed = "#A44B4B", blocked = "#986D9A", cancelled = "#6B7882",
  not_started = "#D5DADF", verified = "#477A66", pending = "#D5DADF", constructed_case = "#557E9B"
)
labels <- c(deterministic = "\u786e\u5b9a\u6027\u8bc4\u5206\u89c4\u5219", manual = "\u4eba\u5de5\u8bc4\u5206", reference_pending = "\u5f15\u7528\u5f85\u6838",
  completed = "\u5df2\u5b8c\u6210", failed = "\u5931\u8d25", blocked = "\u963b\u585e", cancelled = "\u5df2\u53d6\u6d88", not_started = "\u672a\u8fd0\u884c",
  verified = "\u5df2\u4eba\u5de5\u6838\u5bf9", pending = "\u5f85\u6838\u9a8c", constructed_case = "\u6784\u9020\u6848\u4f8b")

save_r_plot <- function(plot, id, width = 7.2, height = 3.7) {
  stem <- file.path(figure_dir, id)
  ragg::agg_png(paste0(stem, ".png"), width = width, height = height, units = "in", res = 400, background = "white")
  print(plot)
  grDevices::dev.off()
  ragg::agg_tiff(paste0(stem, ".tiff"), width = width, height = height, units = "in", res = 600, compression = "lzw", background = "white")
  print(plot)
  grDevices::dev.off()
  svglite::svglite(paste0(stem, ".svg"), width = width, height = height, bg = "white")
  print(plot)
  grDevices::dev.off()
  grDevices::cairo_pdf(paste0(stem, ".pdf"), width = width, height = height, family = font_family, bg = "white")
  print(plot)
  grDevices::dev.off()
}

data <- read.csv(data_file, fileEncoding = "UTF-8", stringsAsFactors = FALSE, check.names = FALSE)

bank <- subset(data, figure_id == "F01" & count > 0)
bank$status <- factor(bank$status, levels = c("deterministic", "manual", "reference_pending"))
module_labels <- c(linear_list = "\u7ebf\u6027\u8868", tree = "\u6811", graph = "\u56fe", sorting = "\u6392\u5e8f")
bank$category <- factor(bank$category, levels = rev(names(module_labels)), labels = rev(unname(module_labels)))
bank_breaks <- intersect(c("deterministic", "manual", "reference_pending"), unique(as.character(bank$status)))
p1 <- ggplot(bank, aes(x = count, y = category, fill = status)) +
  geom_col(width = .68, colour = "white", linewidth = .35) +
  geom_text(data = subset(bank, count >= 3), aes(label = count), position = position_stack(vjust = .5),
            colour = "white", size = 3.2, fontface = "bold", show.legend = FALSE) +
  scale_fill_manual(values = palette, breaks = bank_breaks, labels = unname(labels[bank_breaks]), drop = TRUE) +
  scale_x_continuous(breaks = scales::pretty_breaks(5), expand = expansion(mult = c(0, .04))) +
  labs(title = "\u9996\u6279\u9898\u5e93\u6784\u6210", subtitle = "\u5171 30 \u9879\uff1b\u5f15\u7528\u5f85\u6838\u9898\u4fdd\u7559\u5728\u4eba\u5de5\u8bc4\u5206/\u4e0d\u53ef\u7528\u72b6\u6001",
       x = "\u9898\u76ee\u6570", y = NULL, fill = NULL) +
  theme(legend.position = "bottom")
save_r_plot(p1, "F01-question-bank")

ab_all <- subset(data, figure_id == "F02")
ab <- subset(ab_all, count > 0)
ab$status <- factor(ab$status, levels = c("completed", "failed", "blocked", "cancelled", "not_started"))
ab$category <- factor(ab$category, levels = c("A", "B"))
completed_cells <- sum(ab$count[ab$status == "completed"])
planned_cells <- sum(unique(ab_all[c("category", "denominator")])$denominator)
p2 <- ggplot(ab, aes(x = category, y = count, fill = status)) +
  geom_col(width = .48, colour = "white", linewidth = .35) +
  geom_text(data = subset(ab, count >= 3 & status != "not_started"), aes(label = count),
            colour = "white", size = 3.2, fontface = "bold", position = position_stack(vjust = .5)) +
  geom_text(data = subset(ab, status == "not_started"), aes(label = paste0(count, " \u672a\u8fd0\u884c")),
            colour = "#34424A", size = 3.3, fontface = "bold", position = position_stack(vjust = .5)) +
  scale_fill_manual(values = palette, breaks = levels(ab$status), labels = labels[levels(ab$status)], drop = TRUE) +
  scale_y_continuous(limits = c(0, 42), breaks = seq(0, 40, 10), expand = expansion(mult = c(0, .02))) +
  labs(title = "\u771f\u5b9e A/B \u9a8c\u6536\u8fdb\u5ea6", subtitle = paste0("\u5b9e\u9645\u5b8c\u6210 ", completed_cells, "/", planned_cells, " \u4e2a\u6848\u4f8b\u683c\uff1bDeepProf \u5f00\u53d1\u6848\u4f8b"),
       x = "\u5b9e\u9a8c\u7ec4", y = "\u6848\u4f8b\u683c\u6570", fill = NULL) +
  theme(legend.position = "bottom")
save_r_plot(p2, "F02-ab-acceptance")

pages <- subset(data, figure_id == "F03" & count > 0)
pages$status <- factor(pages$status, levels = c("verified", "pending"))
verified_page_count <- sum(pages$count[pages$status == "verified"])
pending_page_count <- sum(pages$count[pages$status == "pending"])
p3 <- ggplot(pages, aes(x = status, y = count, fill = status)) +
  geom_col(width = .5, colour = "white", linewidth = .35) +
  geom_text(data = subset(pages, status == "pending"), aes(label = paste0(count, " \u9875\u5f85\u6838")),
            colour = "#34424A", size = 3.4, fontface = "bold", position = position_stack(vjust = .5)) +
  annotate("text", x = 1, y = max(20, verified_page_count * .55), label = paste0("\u5df2\u6838\u9a8c ", verified_page_count, " \u9875"), size = 3.1,
           colour = "#34424A", fontface = "bold") +
  scale_fill_manual(values = palette, breaks = levels(pages$status), labels = labels[levels(pages$status)]) +
  scale_y_continuous(limits = c(0, 360), breaks = seq(0, 350, 100), expand = expansion(mult = c(0, .02))) +
  labs(title = "\u914d\u5957\u6559\u6750\u9875\u7801\u6838\u9a8c", subtitle = paste0("\u5df2\u6838\u9a8c ", verified_page_count, "/", verified_page_count + pending_page_count, " \u9875\uff1b\u672a\u4f7f\u7528\u7edf\u4e00\u9875\u7801\u504f\u79fb"),
       x = NULL, y = "PDF \u9875\u6570", fill = NULL) +
  theme(legend.position = "bottom")
save_r_plot(p3, "F03-textbook-page-audit")

fixtures <- subset(data, figure_id == "F04" & count > 0)
fixture_labels <- c(cold_start = "\u51b7\u542f\u52a8", prior_insufficient = "\u5148\u9a8c\u4e0d\u8db3", consecutive_errors = "\u8fde\u7eed\u9519\u8bef",
  hint_then_success = "\u63d0\u793a\u540e\u6210\u529f", insufficient_evidence = "\u8bc1\u636e\u4e0d\u8db3", misconception = "\u6982\u5ff5\u8bef\u89e3",
  active_explanation_request = "\u4e3b\u52a8\u8bb2\u89e3", low_progress = "\u591a\u8f6e\u65e0\u8fdb\u5c55")
fixtures$category <- factor(fixtures$category, levels = rev(names(fixture_labels)), labels = rev(fixture_labels))
p4 <- ggplot(fixtures, aes(x = count, y = category)) +
  geom_col(width = .62, fill = palette[["constructed_case"]]) +
  geom_text(aes(label = count), hjust = -.5, colour = "#34424A", size = 3.3, fontface = "bold") +
  scale_x_continuous(limits = c(0, 6), breaks = 0:6, expand = expansion(mult = c(0, .02))) +
  labs(title = "\u51bb\u7ed3\u7684\u5f00\u53d1\u6784\u9020\u6848\u4f8b", subtitle = "8 \u7c7b \u00d7 \u6bcf\u7c7b 5 \u4f8b\uff1b\u975e\u5b66\u751f\u6837\u672c\uff0c\u4e0d\u4f5c\u4e3a\u771f\u5b9e\u6548\u679c\u6570\u636e",
       x = "\u6848\u4f8b\u6570", y = NULL) +
  theme(legend.position = "none")
save_r_plot(p4, "F04-constructed-case-suite")

turn_file <- file.path(base_dir, "source-data", "turn-performance.csv")
turns <- read.csv(turn_file, fileEncoding = "UTF-8", stringsAsFactors = FALSE, check.names = FALSE)
turns$group <- factor(turns$group, levels = c("A", "B"))
turns$elapsed_ms <- suppressWarnings(as.numeric(turns$elapsed_ms))
turns$total_tokens <- suppressWarnings(as.numeric(turns$total_tokens))

completion <- data.frame(
  group = factor(ab_all$category[ab_all$status %in% c("completed", "failed", "blocked", "cancelled", "not_started")], levels = c("A", "B")),
  status = factor(ab_all$status[ab_all$status %in% c("completed", "failed", "blocked", "cancelled", "not_started")],
                  levels = c("completed", "failed", "blocked", "cancelled", "not_started")),
  count = ab_all$count[ab_all$status %in% c("completed", "failed", "blocked", "cancelled", "not_started")]
)
completion_long <- subset(completion, count > 0)
a_completed <- sum(completion$count[completion$group == "A" & completion$status == "completed"])
b_completed <- sum(completion$count[completion$group == "B" & completion$status == "completed"])
failed_total <- sum(completion$count[completion$status == "failed"])
p5a <- ggplot(completion_long, aes(x = group, y = count, fill = status)) +
  geom_col(width = .48, colour = "white", linewidth = .35) +
  geom_text(data = subset(completion_long, count >= 6 & status != "not_started"),
            aes(label = paste0(count, " ", unname(labels[as.character(status)]))),
            position = position_stack(vjust = .5), colour = "white", size = 3.0, fontface = "bold") +
  geom_text(data = subset(completion_long, count >= 6 & status == "not_started"),
            aes(label = paste0(count, " \u672a\u8fd0\u884c")),
            position = position_stack(vjust = .5), colour = "#34424A", size = 3.0, fontface = "bold") +
  scale_fill_manual(values = palette, breaks = levels(completion_long$status), labels = labels[levels(completion_long$status)], drop = TRUE) +
  scale_y_continuous(limits = c(0, 42), breaks = seq(0, 40, 10), expand = expansion(mult = c(0, .02))) +
  labs(title = "\u6848\u4f8b\u683c\u7ec8\u6001\u5206\u5e03", subtitle = paste0("\u6bcf\u7ec4\u8ba1\u5212 40 \u4e2a\u6848\u4f8b\u683c\uff1bA \u5b8c\u6210 ", a_completed, "/40\uff1bB \u5b8c\u6210 ", b_completed, "/40\uff1b\u5931\u8d25 ", failed_total, " \u683c"), x = NULL, y = "\u6848\u4f8b\u683c\u6570", fill = NULL) +
  theme(legend.position = "bottom")

p5b <- ggplot(subset(turns, is.finite(elapsed_ms)), aes(x = group, y = elapsed_ms, fill = group)) +
  geom_boxplot(width = .46, outlier.shape = NA, alpha = .75, colour = "#34424A", linewidth = .35) +
  geom_point(position = position_jitter(width = .09, height = 0), alpha = .3, size = 1.0, colour = "#34424A") +
  scale_fill_manual(values = c(A = "#315D78", B = "#477A66"), guide = "none") +
  scale_y_continuous(labels = scales::label_number(big.mark = ","), expand = expansion(mult = c(.04, .12))) +
  labs(title = "\u6bcf\u56de\u5408\u5ef6\u8fdf", subtitle = paste0("N=", sum(is.finite(turns$elapsed_ms)), " \u4e2a\u811a\u672c\u5316\u56de\u5408"),
       x = NULL, y = "ms / \u56de\u5408") +
  theme(legend.position = "none")

p5c <- ggplot(subset(turns, is.finite(total_tokens)), aes(x = group, y = total_tokens, fill = group)) +
  geom_boxplot(width = .46, outlier.shape = NA, alpha = .75, colour = "#34424A", linewidth = .35) +
  geom_point(position = position_jitter(width = .09, height = 0), alpha = .3, size = 1.0, colour = "#34424A") +
  scale_fill_manual(values = c(A = "#315D78", B = "#477A66"), guide = "none") +
  scale_y_continuous(labels = scales::label_number(big.mark = ","), expand = expansion(mult = c(.04, .12))) +
  labs(title = "\u6bcf\u56de\u5408 Token \u7528\u91cf", subtitle = paste0("N=", sum(is.finite(turns$total_tokens)), " \u4e2a\u811a\u672c\u5316\u56de\u5408"),
       x = NULL, y = "Token / \u56de\u5408") +
  theme(legend.position = "none")

p5 <- ((p5a / p5b / p5c) +
  plot_layout(heights = c(1, 1, 1), guides = "collect") +
  plot_annotation(tag_levels = "a", title = "\u771f\u5b9e Provider A/B \u8fd0\u884c\u6982\u89c8")) &
  theme(plot.tag = element_text(size = 10, face = "bold"), plot.title = element_text(face = "bold", size = 13))

if (!file.exists(alignment_helper) || !file.exists(alignment_auditor)) {
  stop("Nature Figure patchwork alignment helpers are required; set NATURE_FIGURE_SKILL_ROOT.")
}
source(alignment_helper)
alignment_adapter <- file.path(dirname(dirname(base_dir)), "scripts", "patchwork_panel_alignment_adapter.R")
if (!file.exists(alignment_adapter)) stop("Project patchwork alignment adapter is missing.")
source(alignment_adapter)
alignment_dir <- file.path(figure_dir, "alignment")
dir.create(alignment_dir, recursive = TRUE, showWarnings = FALSE)
require_patchwork_panel_alignment(
  p5,
  manifest_path = file.path(alignment_dir, "F05-ab-runtime-overview.layout.json"),
  report_path = file.path(alignment_dir, "F05-ab-runtime-overview.alignment.json"),
  width_in = 7.2,
  height_in = 7.2,
  panel_ids = c("a", "b", "c"),
  column_groups = list(c("a", "b", "c")),
  audit_script = alignment_auditor,
  overlay_svg = file.path(alignment_dir, "F05-ab-runtime-overview.overlay.svg"),
  strict = TRUE
)
save_r_plot(p5, "F05-ab-runtime-overview", width = 7.2, height = 7.2)

versions <- c(paste("R", getRversion()), paste("ggplot2", packageVersion("ggplot2")),
  paste("patchwork", packageVersion("patchwork")),
  paste("svglite", packageVersion("svglite")), paste("ragg", packageVersion("ragg")),
  paste("font_family", font_family), paste("source_encoding", "ASCII with Unicode escapes"),
  paste("generated_at_utc", format(Sys.time(), tz = "UTC", usetz = TRUE)))
writeLines(versions, file.path(base_dir, "source-data", "plot-runtime.txt"), useBytes = TRUE)
writeLines(capture.output(sessionInfo()), file.path(base_dir, "source-data", "R-session-info.txt"), useBytes = TRUE)
cat("Rendered 5 ggplot2 + patchwork figures to ", figure_dir, "\n", sep = "")
