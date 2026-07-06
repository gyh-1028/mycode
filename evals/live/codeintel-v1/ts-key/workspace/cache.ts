import { cacheKey } from "./keys";
export const lookup = (tenant: string, id: string) => cacheKey(tenant, id);
