# Patch the Nature Figure alignment helper's physical-unit probe for gtable
# layouts containing `null` tracks. grid::convertWidth/Height returns zero for
# those tracks even after drawing; allocate the remaining device dimensions by
# their declared null-unit ratios before writing the same backend-neutral JSON.
write_patchwork_panel_layout <- function(
  plot, manifest_path, width_in, height_in, panel_ids = NULL,
  row_groups = NULL, column_groups = NULL, exemptions = list()
) {
  grob <- patchwork::patchworkGrob(plot)
  panel_rows <- grob$layout[grepl("^panel(?:-[0-9]+)?$", grob$layout$name, perl = TRUE), , drop = FALSE]
  if (nrow(panel_rows) < 2L) stop("At least two patchwork panel cells are required", call. = FALSE)
  panel_rows <- panel_rows[order(panel_rows$t, panel_rows$l), , drop = FALSE]
  if (is.null(panel_ids)) panel_ids <- letters[seq_len(nrow(panel_rows))]
  panel_ids <- as.character(panel_ids)
  if (length(panel_ids) != nrow(panel_rows) || anyDuplicated(panel_ids) || any(!nzchar(panel_ids))) {
    stop("panel_ids must be unique, non-empty, and match measured panels", call. = FALSE)
  }

  probe_path <- tempfile(fileext = ".pdf")
  grDevices::cairo_pdf(probe_path, width = width_in, height = height_in, family = font_family)
  on.exit({
    if (grDevices::dev.cur() > 1L) grDevices::dev.off()
    unlink(probe_path)
  }, add = TRUE)
  grid::grid.newpage()
  grid::grid.draw(grob)
  grid::grid.force()

  resolve_tracks <- function(units, budget_pt, convert) {
    sizes <- convert(units, "pt", valueOnly = TRUE)
    null_tracks <- vapply(seq_along(units), function(i) grid::unitType(units[i]) == "null", logical(1))
    if (any(null_tracks)) {
      ratios <- as.numeric(units[null_tracks])
      remaining <- budget_pt - sum(sizes[!null_tracks])
      if (!is.finite(remaining) || remaining <= 0 || sum(ratios) <= 0) {
        stop("Patchwork null tracks do not fit the requested physical device", call. = FALSE)
      }
      sizes[null_tracks] <- remaining * ratios / sum(ratios)
    }
    if (any(!is.finite(sizes)) || any(sizes < 0)) stop("Patchwork tracks have invalid physical sizes", call. = FALSE)
    sizes
  }

  widths_pt <- resolve_tracks(grob$widths, width_in * 72, grid::convertWidth)
  heights_pt <- resolve_tracks(grob$heights, height_in * 72, grid::convertHeight)
  x_edges <- c(0, cumsum(widths_pt))
  top_edges <- c(0, cumsum(heights_pt))
  total_height_pt <- sum(heights_pt)
  panels <- lapply(seq_len(nrow(panel_rows)), function(i) {
    row <- panel_rows[i, ]
    list(
      id = panel_ids[i],
      bbox_pt = unname(c(x_edges[row$l], total_height_pt - top_edges[row$b + 1L],
                         x_edges[row$r + 1L], total_height_pt - top_edges[row$t])),
      grid_id = "patchwork-grid-1",
      row_start = as.integer(row$t - 1L), row_stop = as.integer(row$b),
      col_start = as.integer(row$l - 1L), col_stop = as.integer(row$r)
    )
  })
  manifest <- list(
    schema_version = 1L, backend = "r-patchwork",
    figure = list(width_pt = width_in * 72, height_pt = height_in * 72),
    panels = panels, exemptions = exemptions
  )
  if (!is.null(row_groups)) manifest$row_groups <- .nature_alignment_groups(row_groups)
  if (!is.null(column_groups)) manifest$column_groups <- .nature_alignment_groups(column_groups)
  dir.create(dirname(manifest_path), recursive = TRUE, showWarnings = FALSE)
  jsonlite::write_json(manifest, manifest_path, auto_unbox = TRUE, pretty = TRUE, digits = NA)
  invisible(manifest)
}
