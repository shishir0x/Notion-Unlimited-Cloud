import Database from "better-sqlite3";
import fs from "fs";

const db = new Database("notion-drive-app/notion_drive.db");

console.log("=== FOLDERS IN DATABASE ===");
const folders = db.prepare("SELECT id, name, type, parent_id, description FROM items WHERE type='folder' LIMIT 50").all();
for (const f of folders) {
  console.log(`Folder: "${f.name}" | ID: ${f.id} | parent_id: ${f.parent_id}`);
}

console.log("\n=== PARENT-CHILD COUNTS ===");
const parentCounts = db.prepare(`
  SELECT p.name as parent_name, i.parent_id, COUNT(*) as child_count 
  FROM items i 
  LEFT JOIN items p ON i.parent_id = p.id 
  GROUP BY i.parent_id 
  ORDER BY child_count DESC 
  LIMIT 20
`).all();
for (const p of parentCounts) {
  console.log(`Parent: "${p.parent_name || 'ORPHAN / ROOT'}" (${p.parent_id}) -> ${p.child_count} children`);
}

console.log("\n=== MEDIA FILES (IMAGES & VIDEOS) ===");
const mediaFiles = db.prepare(`
  SELECT id, name, extension, file_type, file_url, parent_id, description 
  FROM items 
  WHERE extension IN ('.jpg', '.jpeg', '.png', '.mp4', '.mkv', '.webm', '.pdf') 
  LIMIT 20
`).all();
for (const m of mediaFiles) {
  console.log(`Media: "${m.name}" | Ext: ${m.extension} | FileURL: ${m.file_url ? 'YES' : 'NO'} | parent_id: ${m.parent_id}`);
}

console.log("\n=== DISTINCT EXTENSIONS ===");
const exts = db.prepare("SELECT extension, COUNT(*) as count FROM items WHERE type='file' GROUP BY extension ORDER BY count DESC LIMIT 20").all();
console.log(exts);
