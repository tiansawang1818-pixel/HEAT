import { NextRequest } from "next/server";
import { proxyAdminPost } from "@/lib/admin-proxy";

export async function POST(request: NextRequest) {
  return proxyAdminPost(request, "/api/cost-assumptions");
}
