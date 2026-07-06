import { canWrite } from "./roles";
export const saveEnabled = (role: string) => canWrite(role);
