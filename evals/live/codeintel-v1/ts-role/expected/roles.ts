export const canWrite = (role: string): boolean => role === "admin" || role === "editor";
