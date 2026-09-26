#ifndef DEBUG_PRINT_H
#define DEBUG_PRINT_H

#ifdef DEBUG_MODE
#define DEBUG_PRINTLN(X) \
    Serial.print("[DEBUG "); \
    Serial.print(__func__); \
    Serial.print("():"); \
    Serial.print(__LINE__); \
    Serial.print("] "); \
    Serial.println(X);
#else
#define DEBUG_PRINTLN(...)
#endif

#define ERROR_PRINTLN(X, ...)  \
    Serial.print("[ERROR "); \
    Serial.print(__func__); \
    Serial.print("():"); \
    Serial.print(__LINE__); \
    Serial.print("] "); \
    Serial.println(__VA_ARGS__);

#endif // DEBUG_PRINT_H