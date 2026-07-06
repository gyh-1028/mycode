import { calculateTax } from "./tax";
export const total = (subtotal: number) => subtotal + calculateTax(subtotal);
