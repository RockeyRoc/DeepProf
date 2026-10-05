#!/usr/bin/env Rscript
# Reproducible four-figure export for the BKT/RAG evidence report.
if (.Platform$OS.type == 'windows') Sys.setlocale('LC_CTYPE', 'Chinese_China.utf8')
suppressPackageStartupMessages({
  library(ggplot2)
  library(patchwork)
  library(svglite)
})

args <- commandArgs(trailingOnly = TRUE)
root <- if (length(args) >= 1) normalizePath(args[[1]], winslash = "/", mustWork = TRUE) else normalizePath(".", winslash = "/")
out <- file.path(root, "docs/experiments/v0.6.4")
data_dir <- file.path(out, "data")
fig_dir <- file.path(out, "figures")
dir.create(fig_dir, recursive = TRUE, showWarnings = FALSE)
font_family <- "Microsoft YaHei"
theme_set(theme_minimal(base_size = 10.5, base_family = font_family) +
  theme(plot.title = element_text(face = "bold", size = 13, colour = "#19324D"),
        plot.subtitle = element_text(colour = "#52677A", size = 9),
        axis.title = element_text(face = "bold", colour = "#34495E"),
        axis.text = element_text(colour = "#34495E"),
        panel.grid.minor = element_blank(),
        legend.position = "bottom",
        plot.margin = margin(8, 10, 8, 8)))

read_csv <- function(name) read.csv(file.path(data_dir, name), fileEncoding = "UTF-8-BOM", check.names = FALSE, stringsAsFactors = FALSE)
save_triplet <- function(plot, stem, width = 180, height = 125) {
  ggsave(file.path(fig_dir, paste0(stem, ".svg")), plot, width = width, height = height, units = "mm", device = svglite::svglite, bg = "white")
  ggsave(file.path(fig_dir, paste0(stem, ".pdf")), plot, width = width, height = height, units = "mm", device = grDevices::cairo_pdf, bg = "white")
  ggsave(file.path(fig_dir, paste0(stem, ".png")), plot, width = width, height = height, units = "mm", dpi = 600, device = "png", bg = "white")
}

pal_status <- c(available = "#287D8E", historical_public_data = "#4C78A8", historical_test_viewed = "#8E6C9E",
  pending_full_master_access = "#D18F31", relevance_labels_pending = "#D18F31", draft_gold_pending_human = "#D18F31",
  candidate_conditional_eval_complete = "#287D8E", candidate_qrel_missingness_reported = "#4C78A8",
  ai_reviewed_personnel_pending = "#8295A5",
  not_run = "#8A98A6")

# Figure 1 — dataset coverage and evidence status (rows are inventory items, not a common count scale).
coverage <- read_csv("coverage.csv")
coverage$row <- rev(seq_len(nrow(coverage)))
coverage$status_label <- gsub("_", " ", coverage$status)
coverage$coverage_label <- ifelse(is.na(coverage$count) | coverage$count == "", "N/A", format(as.numeric(coverage$count), big.mark = ",", scientific = FALSE, trim = TRUE))
coverage$item <- paste0(coverage$domain, "  ·  ", coverage$unit, "\n", coverage$split, "  ·  n=", coverage$coverage_label)
p1 <- ggplot(coverage, aes(x = 1, y = row, fill = status)) +
  geom_tile(width = 0.045, height = 0.78) +
  geom_text(aes(x = 0.97, label = item), hjust = 1, size = 3.0, family = font_family, colour = "#23384D") +
  geom_text(aes(x = 1.02, label = status_label), hjust = 0, size = 2.8, family = font_family, colour = "#52677A") +
  scale_fill_manual(values = pal_status, guide = "none") +
  scale_x_continuous(limits = c(0.15, 1.85), breaks = NULL) +
  scale_y_continuous(breaks = NULL, expand = expansion(add = 0.65)) +
  labs(title = "数据覆盖与证据状态", subtitle = "数量对应不同单位，按清单逐项标示；颜色表示证据或访问状态。", x = NULL, y = NULL) +
  theme(panel.grid = element_blank(), axis.text = element_blank(), axis.ticks = element_blank(),
        plot.subtitle = element_text(margin = margin(b = 8)))
save_triplet(p1, "figure-01-data-coverage", width = 190, height = 190)
if (length(args) > 1 && args[[2]] == "coverage-only") quit(status=0)

# Figure 2 — stratified BKT AUC difference against the fold-trained global constant.
strata <- read_csv("public-bkt-strata.csv")
method_labels <- c(original_bkt = "原始 BKT", global_em = "全局 EM", concept_em = "概念 EM",
  hierarchical_shrinkage = "收缩 BKT", global_constant = "全局常数", concept_constant = "概念常数")
axis_labels <- c(full_sequence_length = "完整序列长度", prior_attempts = "预测时已有作答", training_concept_coverage = "训练折知识点覆盖")
strata$method_label <- ifelse(strata$method %in% names(method_labels), method_labels[strata$method], strata$method)
strata$axis_label <- axis_labels[strata$axis]
strata$axis_label[is.na(strata$axis_label)] <- strata$axis[is.na(strata$axis_label)]
strata$plot_label <- ifelse(is.na(strata$delta_auc_vs_global_constant), "N/A",
  ifelse(strata$method == "hierarchical_shrinkage" & strata$delta_auc_global_evidence %in% "evidence_insufficient_ci_crosses_zero",
    paste0(sprintf("%.2f", strata$delta_auc_vs_global_constant), "*"), sprintf("%.2f", strata$delta_auc_vs_global_constant)))
strata$stratum <- factor(strata$stratum, levels = c("1-3", "4-10", "11-20", "21+", "0-2", "3-5", "6-9", "10+", "0", "1-39", "40-199", "200+"))
strata$method_label <- factor(strata$method_label, levels = rev(unname(method_labels)))
p2 <- ggplot(strata, aes(x = stratum, y = method_label, fill = delta_auc_vs_global_constant)) +
  geom_tile(colour = "white", linewidth = 0.35) +
  geom_text(aes(label = plot_label), size = 2.6, colour = "#142B3D") +
  facet_wrap(vars(axis_label), ncol = 1, scales = "free_x", strip.position = "left") +
  scale_fill_gradient2(low = "#B44A4A", mid = "#F3F1EA", high = "#287D8E", midpoint = 0, na.value = "#D8DDE2", name = expression(Delta * " AUC")) +
  labs(title = "BKT 分层 AUC 差异", subtitle = "相对训练折全局常数；* 表示收缩 BKT 的配对区间跨零。历史测试仅作回归诊断。", x = "分层", y = NULL) +
  theme(strip.placement = "outside", strip.text.y = element_text(angle = 0, face = "bold", size = 8),
        axis.text.y = element_text(size = 8), axis.text.x = element_text(size = 8),
        panel.spacing.y = unit(0.8, "lines"))
save_triplet(p2, "figure-02-bkt-stratified-differences", width = 185, height = 150)

# Figure 3 — nested OOF calibration metrics and item-EM convergence.
nested <- read_csv("nested-bkt-methods.csv")
em <- read_csv("item-em-convergence.csv")
metric_names <- c("log_loss", "brier", "ece_10_bins")
pretty_methods <- c(nested_selected = "嵌套选择", nested_selected_calibrated = "单调校准", nested_shrinkage_only = "仅收缩",
  item_difficulty = "题目难度", concept_em = "概念 EM", original_bkt = "原始 BKT",
  global_constant = "全局常数", concept_constant = "概念常数")
nested <- nested[nested$method %in% names(pretty_methods), ]
metric_long <- do.call(rbind, lapply(metric_names, function(m) data.frame(method = nested$method, metric = m, value = as.numeric(nested[[m]]))))
metric_long$method_label <- pretty_methods[metric_long$method]
metric_long$method_label[is.na(metric_long$method_label)] <- metric_long$method[is.na(metric_long$method_label)]
metric_long$metric_label <- factor(metric_long$metric, levels = metric_names, labels = c("Log loss ↓", "Brier ↓", "ECE (10 bins) ↓"))
p3a <- ggplot(metric_long, aes(x = value, y = reorder(method_label, value), colour = metric_label)) +
  geom_point(size = 2.4, show.legend = FALSE) +
  facet_wrap(~ metric_label, scales = "free_x", nrow = 1) +
  scale_colour_manual(values = c("#287D8E", "#D18F31", "#8E6C9E")) +
  labs(title = "嵌套开发 OOF 指标", subtitle = "校准与区分能力共同报告；ECE 越低越好。", x = "分数（越低越好）", y = NULL) +
  theme(axis.text.y = element_text(size = 7.5), strip.text = element_text(face = "bold"), panel.spacing.x = unit(1, "lines"))
p3b <- ggplot(em, aes(x = factor(outer_fold), y = em_iterations_used)) +
  geom_col(fill = "#287D8E", width = 0.58) +
  geom_text(aes(label = paste0(em_iterations_used, " rounds; ", converged_concepts, "/", fit_concepts, " converged")),
            hjust = -0.04, size = 2.8, family = font_family, colour = "#34495E") +
  coord_flip(clip = "off") + scale_y_continuous(limits = c(0, 30), breaks = seq(0, 30, 5), expand = expansion(mult = c(0, 0.1))) +
  labs(title = "题目难度 EM 收敛", subtitle = "100 轮上限；5 个外层折均收敛。", x = "外层折", y = "迭代轮数") +
  theme(plot.margin = margin(8, 38, 8, 8), panel.grid.major.y = element_blank())
p3 <- p3a / p3b + plot_layout(heights = c(1.2, 0.85))
save_triplet(p3, "figure-03-calibration-and-convergence", width = 190, height = 190)

# Retired placeholders are reproducible from the archived original script.

# Figure 6 — citation metrics with explicit, distinct denominators.
citation <- read_csv("citation-rates.csv")
citation$metric <- factor(citation$metric, levels = rev(citation$metric))
citation$label <- sprintf("%s / %s  (%.1f%%)", citation$numerator, citation$denominator, 100 * citation$rate)
p6 <- ggplot(citation, aes(x = rate, y = metric)) +
  geom_segment(aes(x = 0, xend = rate, yend = metric), colour = "#C4D2DA", linewidth = 1.2) +
  geom_point(colour = "#287D8E", size = 3) +
  geom_text(aes(label = label), nudge_x = 0.025, hjust = 0, size = 3, colour = "#34495E") +
  scale_x_continuous(limits = c(0, 1.45), breaks = seq(0, 1, .2), labels = scales::percent_format(accuracy = 1)) +
  labs(title = "引用指标拆分", subtitle = "分母各不相同：字段格式、可定位引用、主张—引用配对、事实性主张。AI 辅助判定，未人工校准。", x = "比例", y = NULL) +
  theme(panel.grid.major.y = element_blank(), axis.text.y = element_text(size = 8))
save_triplet(p6, "figure-06-citation-rate-definitions", width = 190, height = 115)

cat("Exported 4 figure groups (SVG/PDF/600-dpi PNG) with ", font_family, " font to ", fig_dir, "\n", sep = "")
