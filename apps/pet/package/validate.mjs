import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const REQUIRED_ACTIONS = [
  "idle",
  "running-right",
  "running-left",
  "waving",
  "jumping",
  "failed",
  "waiting",
  "running",
  "review",
];

export function validatePetPackage(root = dirname(fileURLToPath(import.meta.url))) {
  const manifestPath = join(root, "pet.json");
  if (!existsSync(manifestPath)) throw new Error(`pet_manifest_missing:${manifestPath}`);
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const expectedAtlas = { columns: 8, rows: 9, width: 1536, height: 1872 };
  if (manifest.spritesheetPath !== "spritesheet.webp") throw new Error("pet_spritesheet_path_invalid");
  if (JSON.stringify(manifest.actions) !== JSON.stringify(REQUIRED_ACTIONS)) throw new Error("pet_actions_invalid");
  if (JSON.stringify(manifest.atlas) !== JSON.stringify(expectedAtlas)) throw new Error("pet_atlas_invalid");

  const spritesheetPath = join(root, manifest.spritesheetPath);
  if (!existsSync(spritesheetPath) || statSync(spritesheetPath).size < 1024) {
    throw new Error(`pet_spritesheet_invalid:${spritesheetPath}`);
  }
  return { manifestPath, spritesheetPath, actions: REQUIRED_ACTIONS.length };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const result = validatePetPackage();
  console.log(`validated ${result.actions} pet actions and ${result.spritesheetPath}`);
}
