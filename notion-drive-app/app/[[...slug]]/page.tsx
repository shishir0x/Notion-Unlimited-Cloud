"use client";

// Catch-all route: every drive URL (/, /folder/<id>, /recent, /starred,
// /trash) renders the same client-side drive app, which derives the view
// from usePathname(). Real routes make deep links and back/forward work.
import DrivePage from "@/components/DrivePage";

export default function DriveRoutePage() {
  return <DrivePage />;
}
